import os

from flask import Flask, render_template, request, redirect, url_for, session, flash

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')

# Секретный ключ для подписи сессионных данных.
# В продакшене нужно хранить в безопасном месте (например в переменных окружения).
app.secret_key = "YOUR_SECRET_KEY_HERE"

# "Заглушка": в реальном проекте нужно безопасное хранение паролей
VALID_USERNAME = "iryn_user"
VALID_PASSWORD = "op09op"


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Страница логина. GET показывает форму, POST обрабатывает ввод.
    """
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == VALID_USERNAME and password == VALID_PASSWORD:
            session['logged_in'] = True
            flash("Вы успешно вошли в систему.")
            return redirect(url_for('index'))
        else:
            flash("Неверное имя пользователя или пароль.")
            return redirect(url_for('login'))
    
    # Если GET-запрос, рендерим форму
    return render_template('login.html')


@app.route('/logout')
def logout():
    """
    Разлогинивает пользователя, убирая флаг из сессии.
    """
    session.pop('logged_in', None)
    flash("Вы вышли из системы.")
    return redirect(url_for('login'))


@app.route('/')
def index():
    """
    Главная страница чата.
    Если пользователь не авторизован, перенаправляем на /login.
    """
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    # Передаём URL бэкенда в шаблон (через переменную окружения BACKEND_URL)
    backend_url = os.environ.get('BACKEND_URL', 'http://localhost:8000')
    return render_template('index.html', backend_url=backend_url)


if __name__ == '__main__':
    # В реальном окружении можно запускать под Gunicorn, 
    # но для отладки — так:
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000), debug=True)
