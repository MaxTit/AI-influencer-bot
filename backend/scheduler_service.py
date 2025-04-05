# scheduler_service.py

import os
import json
import time
import datetime
import requests
from typing import Dict, Optional
from google.cloud import tasks_v2  # pip install google-cloud-tasks
from google.api_core.exceptions import AlreadyExists, NotFound
from google.protobuf import field_mask_pb2

from openai_service import OpenAIService

class SchedulerService:
    def __init__(
        self,
        openai_service: OpenAIService,
        project_id: str,
        queue_id: str,
        location: str,
        callback_url: str,
    ):
        """
        :param openai_service: ваш сервис для работы с OpenAI
        :param project_id: GCP Project ID
        :param queue_id: имя очереди Cloud Tasks
        :param location: регион очереди (например, "us-central1")
        :param callback_url: URL, на который Cloud Tasks будет отправлять запрос
        """
        self.openai_service = openai_service
        self.project_id = project_id
        self.queue_id = queue_id
        self.location = location
        self.callback_url = callback_url

        self.client = tasks_v2.CloudTasksClient()
        self.firebase_base_url = "https://relationship-with-iryn-default-rtdb.firebaseio.com"

    def schedule_answer(self, user_id: str, time_answer: int) -> str:
        # Путь к очереди
        parent = self.client.queue_path(self.project_id, self.location, self.queue_id)

        # Сформируем уникальное имя задачи. 
        # user-{user_id}, чтобы была одна задача на каждого userId.
        task_id = f"user-{user_id}"
        # Полное имя (resource name) задачи
        task_name = self.client.task_path(self.project_id, self.location, self.queue_id, task_id)

        payload_data = {"userId": user_id}
        payload_bytes = json.dumps(payload_data).encode("utf-8")

        schedule_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=time_answer)
        schedule_time = schedule_time.replace(tzinfo=datetime.timezone.utc)

        task = {
            "name": task_name,  # <-- Явно указываем имя
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.callback_url,
                "headers": {"Content-Type": "application/json"},
                "body": payload_bytes,
            },
            "schedule_time": schedule_time,
        }

        try:
            response = self.client.create_task(parent=parent, task=task)
            return response.name
        except AlreadyExists:
            # Если задача с таким именем уже существует
            print(f"Task for userId={user_id} is already scheduled.")
            raise ValueError(f"Task for userId={user_id} is already scheduled.")

    def run_answer_job(self, user_id: str):
        """
        Вызывается при поступлении запроса от Cloud Tasks (через /tasks/answer-job).
        1. Находит все сообщения /Messages, где userId == user_id и isAnswer == false.
        2. Собирает их в одну строку.
        3. Смотрит /users/{userId} -> aiTreadId.
        4. Отправляет в OpenAI -> получает ответ.
        5. Записывает ответ в /Messages как isAnswer=true, isBot=true.
        6. Все старые сообщения isAnswer=false -> true.
        """
        print(f"[run_answer_job] for userId={user_id}")

        # 1. Собираем сообщения
        messages_data = self.fetch_user_messages(user_id)
        if not messages_data:
            print("No messages found in /Messages or all answered.")
            return

        pending_msgs = sorted(
            (m for m in messages_data if m.get("isAnswer") == False),
            key=lambda x: x.get("dateSend", 0)
        )
        if not pending_msgs:
            print("No messages with isAnswer=false.")
            return

        # 2. Собираем текст
        # Markdown-цитаты
        combined_text = "\n\n".join(f"> {msg['message']}" for msg in pending_msgs)


        # 3. Получаем aiTreadId
        ai_thread_id = self.fetch_user_ai_thread_id(user_id)
        if not ai_thread_id:
            print(f"No aiTreadId found for userId={user_id}")
            return

        # 4. Отправляем запрос в OpenAI
        response = self.openai_service.send_prompt(ai_thread_id, combined_text)
        assistant_message = response.get("message", "No response")

        # 5. Записываем ответ
        self.store_bot_message(user_id, assistant_message)

        # 6. Помечаем старые сообщения как isAnswer=true
        self.mark_messages_answered(pending_msgs)

        print(f"Job completed for userId={user_id}")

    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ---

    def fetch_user_messages(self, user_id: str):
        """
        Получаем все Messages, где userId == user_id.
        Используем запрос с orderBy="userId"&equalTo="...".
        """
        url = (
            f'{self.firebase_base_url}/Messages.json'
            f'?orderBy="userId"&equalTo="{user_id}"'
        )
        resp = requests.get(url)
        if resp.status_code != 200:
            print("Error fetching messages:", resp.text)
            return []

        data = resp.json() or {}
        results = []
        for key, val in data.items():
            val["firebaseKey"] = key
            results.append(val)
        return results

    def fetch_user_ai_thread_id(self, user_id: str) -> Optional[str]:
        """
        Возвращаем aiTreadId из /users, где userId == user_id.
        """
        url = (
            f'{self.firebase_base_url}/users.json'
            f'?orderBy="userId"&equalTo="{user_id}"'
        )
        resp = requests.get(url)
        if resp.status_code != 200:
            print("Error fetching user data:", resp.text)
            return None

        data = resp.json() or {}
        for k, v in data.items():
            return v.get("aiTreadId")
        return None

    def store_bot_message(self, user_id: str, bot_text: str):
        """
        Добавляем запись в /Messages с isAnswer=true, isBot=true.
        """
        url = f"{self.firebase_base_url}/Messages.json"
        timestamp = int(time.time() * 1000)
        payload = {
            "userId": user_id,
            "message": bot_text,
            "isAnswer": True,
            "isBot": True,
            "dateSend": timestamp
        }
        resp = requests.post(url, json=payload)
        if resp.status_code >= 300:
            print("[store_bot_message] Error:", resp.text)

    def mark_messages_answered(self, messages_list: list):
        """
        Всем записям из messages_list, у которых isAnswer=false, ставим true
        """
        for msg in messages_list:
            if not msg.get("isAnswer"):
                firebase_key = msg["firebaseKey"]
                patch_url = f"{self.firebase_base_url}/Messages/{firebase_key}.json"
                payload = {"isAnswer": True}
                resp = requests.patch(patch_url, json=payload)
                if resp.status_code >= 300:
                    print(f"[mark_messages_answered] Error for {firebase_key}:", resp.text)
