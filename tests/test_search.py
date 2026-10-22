import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from jitter_runner.search import candidate_id, choose_candidate, configuration, normalize_space
from jitter_runner.spec import ExperimentError


def experiment(adapter="shared-index-v1"):
    return {"schema_version": 2, "application": {"adapter": adapter, "workers": 4, "queue_capacity": 8}}


def search(strategy="guided"):
    return {"strategy": strategy, "seed": 17,
            "parameters": {"workers": [1, 4, 16], "queue_capacity": [8, 64],
                           "implementation": ["coarse", "narrow"]}, "exclude": []}


def trial(config, effect, status="feasible", cost=1):
    return {"configuration": copy.deepcopy(config), "status": status, "effect": effect, "cost_seconds": cost}


class SearchSpaceTests(unittest.TestCase):
    def test_ranges_are_canonical_and_baseline_is_present(self):
        spec = search()
        spec["parameters"] = {"workers": {"min": 1, "max": 8, "step": 3}}
        space = normalize_space(experiment(), spec)
        self.assertEqual([point["workers"] for point in space], [1, 4, 7])
        self.assertIn(configuration(experiment()), space)
        spec["parameters"] = {"workers": [1, 16]}
        self.assertEqual([point["workers"] for point in normalize_space(experiment(), spec)], [1, 4, 16])

    def test_omitted_baseline_adds_only_that_point(self):
        spec = search()
        spec["parameters"] = {"workers": [1], "queue_capacity": [64], "implementation": ["narrow"]}
        space = normalize_space(experiment(), spec)
        self.assertEqual(len(space), 2)
        self.assertIn(configuration(experiment()), space)

    def test_exclusions_are_partial_and_preserve_baseline(self):
        spec = search()
        spec["exclude"] = [{"workers": 1, "implementation": "coarse"}, {"queue_capacity": 64}]
        space = normalize_space(experiment(), spec)
        self.assertTrue(all(point["queue_capacity"] == 8 for point in space))
        self.assertNotIn({"workers": 1, "queue_capacity": 8, "implementation": "coarse"}, space)
        spec["exclude"] = [{"workers": 4}]
        with self.assertRaisesRegex(ExperimentError, "baseline"):
            normalize_space(experiment(), spec)

    def test_adapter_specific_implementations_and_legacy_baseline(self):
        self.assertEqual(configuration(experiment())["implementation"], "coarse")
        app = experiment("json-projection-v1")
        self.assertEqual(configuration(app)["implementation"], "map")
        spec = search()
        spec["parameters"]["implementation"] = ["map", "ordered"]
        self.assertEqual(len(normalize_space(app, spec)), 12)
        with self.assertRaises(ExperimentError):
            normalize_space(experiment(), spec)
        app["schema_version"] = 3
        app["application"]["implementation"] = "ordered"
        self.assertEqual(configuration(app)["implementation"], "ordered")

    def test_malformed_spaces_fail(self):
        mutations = [
            lambda spec: spec.update(extra=1),
            lambda spec: spec.update(strategy="mystery"),
            lambda spec: spec.update(seed=True),
            lambda spec: spec.update(seed=-1),
            lambda spec: spec.update(parameters=[]),
            lambda spec: spec["parameters"].update(light_rounds=[1]),
            lambda spec: spec["parameters"].update(workers=[]),
            lambda spec: spec["parameters"].update(workers=[True]),
            lambda spec: spec["parameters"].update(workers=[4, 4]),
            lambda spec: spec["parameters"].update(workers=[0]),
            lambda spec: spec["parameters"].update(workers={"min": 1, "max": 4, "step": 0}),
            lambda spec: spec["parameters"].update(workers={"min": 4, "max": 1, "step": 1}),
            lambda spec: spec["parameters"].update(workers={"min": 1, "max": 4, "step": 1, "other": 2}),
            lambda spec: spec["parameters"].update(implementation={"min": 1, "max": 4, "step": 1}),
            lambda spec: spec.update(exclude={}),
            lambda spec: spec.update(exclude=[{}]),
            lambda spec: spec.update(exclude=[{"heavy_rounds": 2}]),
            lambda spec: spec.update(exclude=[{"workers": 2}]),
            lambda spec: spec.update(exclude=[{"workers": False}]),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                spec = search()
                mutation(spec)
                with self.assertRaises(ExperimentError):
                    normalize_space(experiment(), spec)

    def test_raw_cartesian_bound_applies_before_exclusions(self):
        spec = search()
        spec["parameters"] = {"workers": {"min": 1, "max": 256, "step": 1},
                              "queue_capacity": {"min": 1, "max": 17, "step": 1}}
        spec["exclude"] = [{"workers": 1}]
        with self.assertRaisesRegex(ExperimentError, "4096"):
            normalize_space(experiment(), spec)
        spec["parameters"]["queue_capacity"]["max"] = 16
        spec["exclude"] = []
        self.assertEqual(len(normalize_space(experiment(), spec)), 4096)

    def test_large_range_is_rejected_before_expansion(self):
        spec = search()
        spec["parameters"] = {"queue_capacity": {"min": 1, "max": 1000000, "step": 1}}
        with self.assertRaisesRegex(ExperimentError, "4096"):
            normalize_space(experiment(), spec)

    def test_ids_ignore_dictionary_order(self):
        baseline = configuration(experiment())
        reverse = dict(reversed(list(baseline.items())))
        self.assertEqual(candidate_id(baseline), candidate_id(reverse))
        self.assertEqual(len(candidate_id(baseline)), 12)
        reverse["workers"] += 1
        self.assertNotEqual(candidate_id(baseline), candidate_id(reverse))


class SearchDecisionTests(unittest.TestCase):
    def test_queue_guidance_tests_registered_serialization_alternative_in_v2(self):
        baseline = configuration(experiment())
        space = normalize_space(experiment(), search())
        hypothesis = [{"id": "queue_pressure", "tail_component_fraction": .8}]
        legacy, _ = choose_candidate(space, baseline, [], hypothesis, "guided", 17, model_version=1)
        current, reason = choose_candidate(space, baseline, [], hypothesis, "guided", 17, model_version=2)
        self.assertEqual(legacy, {**baseline, "workers": 16})
        self.assertEqual(current, {**baseline, "implementation": "narrow"})
        self.assertIn("registered serialized resource", reason["basis"])
        for strategy in ("grid", "random", "local"):
            self.assertEqual(choose_candidate(space, baseline, [], hypothesis, strategy, 17, model_version=1),
                             choose_candidate(space, baseline, [], hypothesis, strategy, 17, model_version=2))

    def setUp(self):
        self.baseline = configuration(experiment())
        self.space = normalize_space(experiment(), search())

    def choose(self, history=(), hypotheses=(), strategy="guided", seed=17):
        return choose_candidate(self.space, self.baseline, list(history), hypotheses, strategy, seed)

    def test_contention_and_queue_evidence_change_the_first_test(self):
        contention, lock_reason = self.choose(hypotheses=[{"id": "shared_lock_contention", "tail_component_fraction": .8}])
        queue, queue_reason = self.choose(hypotheses=[{"id": "queue_pressure", "tail_component_fraction": .8}])
        self.assertEqual(contention, {**self.baseline, "implementation": "narrow"})
        self.assertEqual(queue, {**self.baseline, "workers": 16})
        self.assertEqual(lock_reason["mode"], "exploitation")
        self.assertNotEqual(lock_reason["hypothesis_support"], queue_reason["hypothesis_support"])

    def test_evidence_moves_the_incumbent_and_local_neighborhood(self):
        promising = {**self.baseline, "workers": 1}
        chosen, reason = self.choose([trial(promising, .3)], strategy="local")
        self.assertEqual(reason["incumbent"], promising)
        self.assertEqual(chosen["workers"], 1)
        self.assertNotEqual(chosen, promising)
        self.assertIsNotNone(reason["score"])

    def test_failed_positive_effect_does_not_become_incumbent(self):
        failed = {**self.baseline, "workers": 1}
        for status in ("infeasible", "constraint_violation", "inconclusive"):
            with self.subTest(status=status):
                chosen, reason = self.choose([trial(failed, .9, status)])
                self.assertEqual(reason["incumbent"], self.baseline)
                self.assertNotEqual(chosen, failed)
                self.assertEqual(reason["observed_marginals"], [])

    def test_every_strategy_avoids_baseline_and_all_retrials(self):
        for strategy in ("guided", "random", "grid", "local"):
            history = []
            identities = set()
            for _ in range(len(self.space) + 1):
                chosen, reason = self.choose(history, strategy=strategy)
                if chosen is None:
                    break
                identity = candidate_id(chosen)
                self.assertNotEqual(chosen, self.baseline)
                self.assertNotIn(identity, identities)
                identities.add(identity)
                history.append(trial(chosen, None, "failed"))
            else:
                self.fail("search did not exhaust its finite space")
            if strategy != "local":
                self.assertEqual(len(identities), len(self.space) - 1)
            else:
                self.assertEqual(reason["mode"], "local_exhausted")

    def test_random_order_is_seeded_and_does_not_depend_on_list_order(self):
        selected, _ = self.choose(strategy="random")
        reverse, _ = choose_candidate(list(reversed(self.space)), self.baseline, [], [], "random", 17)
        self.assertEqual(selected, reverse)
        orders = []
        for seed in (17, 99):
            history, order = [], []
            while True:
                chosen, _ = self.choose(history, strategy="random", seed=seed)
                if chosen is None:
                    break
                order.append(candidate_id(chosen))
                history.append(trial(chosen, 0))
            orders.append(order)
        self.assertNotEqual(orders[0], orders[1])
        self.assertEqual(set(orders[0]), set(orders[1]))

    def test_every_third_guided_selection_explores(self):
        history = [trial(self.space[0], 0), trial(self.space[-1], -.1)]
        chosen, reason = self.choose(history)
        self.assertEqual(reason["mode"], "exploration")
        self.assertEqual((chosen, reason), self.choose(history))

    def test_numeric_locality_uses_rank_not_magnitude(self):
        spec = search()
        spec["parameters"] = {"queue_capacity": [1, 8, 1000000]}
        space = normalize_space(experiment(), spec)
        history = [trial({**self.baseline, "queue_capacity": 1}, 0)]
        chosen, _ = choose_candidate(space, self.baseline, history, [], "local", 17)
        self.assertEqual(chosen["queue_capacity"], 1000000)

    def test_marginal_effect_and_cost_are_recorded(self):
        one = {**self.baseline, "workers": 1}
        wide = {**self.baseline, "workers": 16}
        chosen, reason = self.choose([trial(one, -.1, cost=2)], hypotheses=["shared_lock_contention"])
        self.assertIsNotNone(reason["estimated_cost_seconds"])
        self.assertIsNotNone(reason["score"])
        # An increasing worker move predicts the measured positive slope.
        only = [self.baseline, one, wide]
        chosen, learned = choose_candidate(only, self.baseline, [trial(one, -.1, cost=2)], [], "guided", 17)
        self.assertEqual(chosen, wide)
        self.assertGreater(learned["observed_marginals"][0]["predicted_effect"], 0)

    def test_malformed_history_and_duplicate_spaces_fail(self):
        with self.assertRaises(ExperimentError):
            choose_candidate(self.space + [self.space[0]], self.baseline, [], [], "guided", 1)
        for entry in ({}, trial(self.space[0], float("nan")), trial(self.space[0], 0, cost=-1)):
            with self.subTest(entry=entry), self.assertRaises(ExperimentError):
                self.choose([entry])


if __name__ == "__main__":
    unittest.main()
