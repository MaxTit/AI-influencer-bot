# main.py
from typing import Dict
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn
import traceback

from openai_service import OpenAIService
from fastapi.middleware.cors import CORSMiddleware

# Наш новый сервис планирования, использующий Cloud Tasks
from scheduler_service import SchedulerService

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class MessageInput(BaseModel):
    thread_id: str
    message: str

class ScheduleRequest(BaseModel):
    userId: str
    timeAnswer: int

openai_service = OpenAIService()

# Инициализируем SchedulerService
# Значения project_id, queue_id, location, callback_url вы должны указать сами
PROJECT_ID = "ai-influencer-bot-73"
QUEUE_ID = "answer-ai"
LOCATION = "us-central1"
# В callback_url указывайте URL вашего Cloud Run (или другой хост), заканчивающийся на /tasks/answer-job
CALLBACK_URL = "https://ai-influencer-bot-backend-702470178997.us-central1.run.app/tasks/answer-job"

scheduler_service = SchedulerService(
    openai_service=openai_service,
    project_id=PROJECT_ID,
    queue_id=QUEUE_ID,
    location=LOCATION,
    callback_url=CALLBACK_URL
)

@app.get("/network-check")
async def network_check():
    try:
        import socket
        sock = socket.create_connection(("api.openai.com", 443), timeout=5)
        sock.close()
        return {"status": "reachable"}
    except Exception as e:
        raise HTTPException(500, f"Connection failed: {str(e)}")

@app.post("/create-thread/")
async def create_thread():
    """Создаёт тред и возвращает его ID."""
    try:
        thread_id = openai_service.create_thread()
        return {"thread_id": thread_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send-message")
async def send_message(data: MessageInput):
    """Отправляет сообщение в OpenAI Assistant в рамках указанного thread_id."""
    try:
        response = openai_service.send_prompt(data.thread_id, data.message)
        return {"thread_id": data.thread_id, **response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get-messages/{thread_id}")
async def get_messages(thread_id: str):
    try:
        messages = openai_service.get_chat_history(thread_id)
        return {"thread_id": thread_id, "messages": messages}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/feetback")
async def process_assistant_feetback(request: dict):
    try:
        thread_id = request.get("thread_id")
        if not thread_id:
            raise HTTPException(status_code=400, detail="Missing 'thread_id' in request body")
        return openai_service.process_assistant_feetback(thread_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===============================
#       НОВЫЕ РОУТЫ
# ===============================

@app.post("/schedule-answer")
async def schedule_answer(data: ScheduleRequest):
    try:
        print(f"[schedule-answer] Received: {data}")
        task_name = scheduler_service.schedule_answer(
            user_id=data.userId,
            time_answer=data.timeAnswer
        )
        print(f"[schedule-answer] Task created: {task_name}")
        return {"status": "ok", "taskName": task_name}
    except Exception as e:
        print(f"[schedule-answer] Exception: {e}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/tasks/answer-job")
async def answer_job(request: Request):
    """
    Этот эндпойнт вызывается Cloud Tasks через POST запрос.
    Тело запроса содержит JSON: {"userId": "..."}.
    Здесь вызываем scheduler_service.run_answer_job(userId).
    """
    # В реальном проекте необходимо проверять заголовки 
    # 'X-CloudTasks-...' и аутентификацию, чтобы этот эндпойнт 
    # не был доступен извне!
    data = await request.json()
    user_id = data.get("userId")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing userId in task payload")
    
    # Запускаем логику
    try:
        scheduler_service.run_answer_job(user_id)
        return {"status": "done"}
    except Exception as e:
        print(f"[answer_job] Exception for userId={user_id}: {e}")
        print(traceback.format_exc())
        print(f"[answer_job] Incoming payload: {data}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
