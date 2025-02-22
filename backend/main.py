from typing import Dict
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from openai_service import OpenAIService

from fastapi.middleware.cors import CORSMiddleware  # Add this import

app = FastAPI()

# Add CORS middleware
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

openai_service = OpenAIService()


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
        thread_id = openai_service.create_thread()  # Убрали await
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
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
