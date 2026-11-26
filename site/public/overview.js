const button = document.getElementById('copy-command');
const command = document.getElementById('local-command');
const status = document.getElementById('copy-status');

if (navigator.clipboard?.writeText) {
  button.hidden = false;
  let reset;
  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(command.textContent);
      button.textContent = 'Copied';
      status.textContent = 'Command copied to the clipboard.';
    } catch {
      const selection = window.getSelection();
      selection.selectAllChildren(command);
      status.textContent = 'Command selected. Copy it with the keyboard or context menu.';
      button.textContent = 'Selected';
    }
    clearTimeout(reset);
    reset = setTimeout(() => {
      button.textContent = 'Copy';
      status.textContent = '';
    }, 2000);
  });
}
