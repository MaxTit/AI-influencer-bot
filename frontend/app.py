import os

from flask import Flask, render_template

app = Flask(__name__,
            template_folder='templates',  # Explicit path
            static_folder='static')

@app.route('/')
def index():
    return render_template('index.html', 
                         backend_url=os.environ.get('BACKEND_URL', 'http://localhost:8000'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 5000))