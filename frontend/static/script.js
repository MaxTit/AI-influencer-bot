// script.js
// ----------------------
// ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
// ----------------------
let currentThreadId = localStorage.getItem('threadId') || null;

// ----------------------
// DOM-функции для чата
// ----------------------
function addMessage(text, role) {
    const messagesDiv = document.getElementById('messages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;
    messageDiv.innerHTML = `
        <div class="message-content">${text}</div>
    `;
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function clearMessages() {
    document.getElementById('messages').innerHTML = '';
}

// ----------------------
// API: Создание нового треда
// ----------------------
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

// ----------------------
// API: Отправка сообщения
// ----------------------
async function sendMessage() {
    const messageInput = document.getElementById('message-input');
    const message = messageInput.value.trim();
    
    if (!message) return;
    if (!currentThreadId) {
        alert('Please select a user or create a thread first!');
        return;
    }

    try {
        addMessage(message, 'user');
        messageInput.value = '';
        
        const response = await fetch(`${API_BASE_URL}/send-message`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

// ----------------------
// API: Загрузка сообщений (история чата)
// ----------------------
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

// ----------------------
// ЗАГРУЗКА СПИСКА ПОЛЬЗОВАТЕЛЕЙ ИЗ FIREBASE
// ----------------------
async function loadUsers() {
    try {
        // Простой GET-запрос в Realtime Database с .json
        const response = await fetch(FIREBASE_USERS_URL);
        if (!response.ok) {
            throw new Error(`Failed to fetch users from Firebase: ${response.statusText}`);
        }
        
        // Ответ будет JSON-объект вида:
        // {
        //   "someUserId": { aiTreadId, avatarUrl, dateLastMessage, dateRegistration, email, name, userId, ... },
        //   "anotherUserId": { ... }
        // }
        const data = await response.json();
        
        if (!data) {
            console.warn("No users found in Firebase DB");
            return;
        }

        // Превращаем объект в массив, чтобы удобнее отрисовать
        const users = Object.entries(data).map(([key, value]) => {
            return {
                userId: key,
                ...value
            };
        });

        renderUserList(users);
    } catch (error) {
        console.error('Error loading users:', error);
        alert('Failed to load users. Check console for details.');
    }
}

function formatDate(dateString) {
    if (!dateString) return '';

    const date = new Date(dateString); // Парсим ISO-дату

    // Извлекаем компоненты даты
    const dd = String(date.getDate()).padStart(2, '0');
    const mm = String(date.getMonth() + 1).padStart(2, '0'); // месяцы 0-11
    const yyyy = date.getFullYear();

    const hh = String(date.getHours()).padStart(2, '0');
    const min = String(date.getMinutes()).padStart(2, '0');
    const ss = String(date.getSeconds()).padStart(2, '0');

    // Склеиваем строку в формате "DD-MM-YYYY HH:MM:SS"
    return `${dd}-${mm}-${yyyy} ${hh}:${min}:${ss}`;
}


// ----------------------
// ОТОБРАЖЕНИЕ СПИСКА ПОЛЬЗОВАТЕЛЕЙ
// ----------------------
function renderUserList(users) {
    const userListDiv = document.getElementById('user-list');
    userListDiv.innerHTML = ''; // очистим панель

    users.forEach(user => {
        const userDiv = document.createElement('div');
        userDiv.className = 'user-item';

        // Отображаем имя + короткая информация
        // dateLastMessage и dateRegistration
        userDiv.innerHTML = `
          <div class="user-name">${user.name || user.userId}</div>
          <div class="user-info">
            <b>Last Msg:</b> ${formatDate(user.dateLastMessage) || '-'}<br>
            <b>Reg:</b> ${formatDate(user.dateRegistration) || '-'}
          </div>
        `;

        // Клик по пользователю -> переключаемся на него
        userDiv.addEventListener('click', () => {
            selectUser(user);
        });

        userListDiv.appendChild(userDiv);
    });
}

// ----------------------
// ВЫБОР КОНКРЕТНОГО ПОЛЬЗОВАТЕЛЯ
// ----------------------
function selectUser(user) {
    // aiTreadId из Firebase
    currentThreadId = user.aiTreadId;
    localStorage.setItem('threadId', currentThreadId || '');

    // Устанавливаем аватар и имя в чате
    const avatarImg = document.getElementById('user-avatar');
    avatarImg.src = user.avatarUrl || '';

    const userNameSpan = document.getElementById('user-name');
    userNameSpan.textContent = user.name || user.userId;

    // Устанавливаем даты в шапке чата
    document.getElementById('user-last-message').textContent = formatDate(user.dateLastMessage) || '';
    document.getElementById('user-reg-date').textContent = formatDate(user.dateRegistration) || '';

    // Загружаем историю сообщений
    clearMessages();
    loadMessages();
}

// ----------------------
// ИНИЦИАЛИЗАЦИЯ ПРИ ЗАГРУЗКЕ СТРАНИЦЫ
// ----------------------
document.addEventListener('DOMContentLoaded', () => {
    // Загрузить список пользователей из Firebase
    loadUsers();

    // Если при прошлой загрузке был выбран тред, восстановим и загрузим
    if (currentThreadId) {
        loadMessages();
    }
});
