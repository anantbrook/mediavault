// Default MediaVault URL
let mvUrl = "http://localhost:5050";

// Load user-configured URL from storage
chrome.storage.sync.get(["mvUrl"], (res) => {
    if (res.mvUrl) {
        mvUrl = res.mvUrl;
    }
});

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: "send-to-mediavault",
        title: "Download to MediaVault",
        contexts: ["image", "video", "link"]
    });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "send-to-mediavault") {
        const urlToDownload = info.srcUrl || info.linkUrl;
        
        if (!urlToDownload) return;

        fetch(`${mvUrl}/api/download`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                url: urlToDownload,
                quality: "max"
            })
        })
        .then(response => response.json())
        .then(data => {
            if(data.ok) {
                console.log("Started downloading to MediaVault:", data.dl_id);
                // Optionally show a notification here
            } else {
                console.error("MediaVault error:", data.error);
            }
        })
        .catch(error => {
            console.error("Failed to connect to MediaVault:", error);
        });
    }
});

// Update URL when changed in settings
chrome.storage.onChanged.addListener((changes, namespace) => {
    if (changes.mvUrl) {
        mvUrl = changes.mvUrl.newValue;
    }
});
