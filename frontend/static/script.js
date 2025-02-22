// script.js

// Initialize global variables
let currentThreadId = localStorage.getItem('threadId') || null;

// DOM manipulation functions
function addMessage(text, role) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;
    messageDiv.innerHTML = `
        <div class="message-content">${text}</div>
        <div class="message-time">${new Date().toLocaleTimeString()}</div>
    `;
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function clearMessages() {
    document.getElementById('messages').innerHTML = '';
}

// API interaction functions
async function createThread() {
    try {
        const response = await fetch(`${API_BASE_URL}/create-thread/`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        currentThreadId = data.thread_id;
        localStorage.setItem('threadId', currentThreadId);
        clearMessages();
        alert(`New thread created: ${currentThreadId}`);
        loadMessages();
    } catch (error) {
        console.error('Error creating thread:', error);
        alert('Failed to create new thread. Please try again.');
    }
}

async function sendMessage() {
    const messageInput = document.getElementById('message-input');
    const message = messageInput.value.trim();
    
    if (!message) return;
    
    if (!currentThreadId) {
        alert('Please create a thread first!');
        return;
    }

    try {
        addMessage(message, 'user');
        messageInput.value = '';
        
        const response = await fetch(`${API_BASE_URL}/send-message`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                thread_id: currentThreadId,
                message: message
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        addMessage(data.message, 'assistant');
    } catch (error) {
        console.error('Error sending message:', error);
        alert('Failed to send message. Please try again.');
    }
}

async function loadMessages() {
    if (!currentThreadId) return;

    try {
        const response = await fetch(`${API_BASE_URL}/get-messages/${currentThreadId}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        displayMessages(data.messages);
    } catch (error) {
        console.error('Error loading messages:', error);
        alert('Failed to load messages. Please refresh the page.');
    }
}

function displayMessages(messages) {
    clearMessages();
    messages.forEach(msg => {
        addMessage(msg.content, msg.role);
    });
}

async function updateSystemPrompt() {
    try {
        const response = await fetch(`${API_BASE_URL}/update-system-prompt`, {
            method: 'POST'
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();
        alert('System prompt updated: ' + data.instruction);
    } catch (error) {
        console.error('Error updating system prompt:', error);
        alert('Failed to update system prompt. Check console for details.');
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', () => {
    // Handle Enter key in message input
    document.getElementById('message-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            sendMessage();
        }
    });

    // Load existing messages on page load
    if (currentThreadId) {
        loadMessages();
    }
});