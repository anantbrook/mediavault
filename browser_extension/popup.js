document.addEventListener('DOMContentLoaded', () => {
    const input = document.getElementById('urlInput');
    const saveBtn = document.getElementById('saveBtn');
    const status = document.getElementById('status');

    chrome.storage.sync.get(["mvUrl"], (res) => {
        if (res.mvUrl) {
            input.value = res.mvUrl;
        } else {
            input.value = "http://localhost:5050";
        }
    });

    saveBtn.addEventListener('click', () => {
        let url = input.value.trim();
        // Remove trailing slash
        if (url.endsWith('/')) {
            url = url.slice(0, -1);
        }
        
        chrome.storage.sync.set({ mvUrl: url }, () => {
            status.textContent = "Settings saved!";
            setTimeout(() => { status.textContent = ""; }, 2000);
        });
    });
});
