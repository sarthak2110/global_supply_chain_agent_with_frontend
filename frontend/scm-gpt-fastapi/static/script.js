async def sendQuickQuery(text) {
    document.getElementById('userInput').value = text;
    sendMessage();
}

async function sendMessage() {
    const inputField = document.getElementById('userInput');
    const message = inputField.value;
    if (!message) return;

    // Clear input
    inputField.value = "";
    inputField.placeholder = "Processing query...";

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message })
        });
        
        const data = await response.json();
        
        // Simple alert for now - you can expand this to append chat bubbles
        alert("Agent Response:\n" + data.response);
        inputField.placeholder = "Ask about inventory, lead times, or disruptions...";
        
    } catch (error) {
        console.error("Error:", error);
        inputField.placeholder = "Error connecting to Brain.";
    }
}

// Allow Enter key to send
document.getElementById("userInput").addEventListener("keypress", function(event) {
    if (event.key === "Enter") {
        sendMessage();
    }
});