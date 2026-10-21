"""Bounded subprocess execution with descendant cleanup on POSIX hosts."""

import os
import signal
import subprocess


def _stop(process):
    # Builds and application adapters may launch children. Killing only Popen's
    # direct child lets those children outlive a timed-out experiment.
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # A child that deliberately creates a different session can retain a
        # pipe. Do not let pipe cleanup turn a deadline into an unbounded wait.
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


def run_command(args, timeout, stdout=None, stderr=None, capture_output=False,
                text=False, check=False):
    """Run an argument vector without a shell and retain subprocess semantics.

    POSIX descendants inherit a separate process group, which is killed on a
    timeout or interrupt. This is lifecycle cleanup, not an isolation sandbox;
    applications that detach into another session require adapter-specific care.
    """
    if isinstance(args, (str, bytes)) or not args:
        raise ValueError("command must be a nonempty argument vector")
    if capture_output:
        if stdout is not None or stderr is not None:
            raise ValueError("capture_output cannot be combined with stdout or stderr")
        stdout, stderr = subprocess.PIPE, subprocess.PIPE
    process = subprocess.Popen(args, stdout=stdout, stderr=stderr, text=text,
                               start_new_session=os.name == "posix")
    try:
        output, errors = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        _stop(process)
        raise
    result = subprocess.CompletedProcess(args, process.returncode, output, errors)
    if check:
        result.check_returncode()
    return result
