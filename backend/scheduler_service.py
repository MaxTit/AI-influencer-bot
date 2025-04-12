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
        """
        1. Проверяем, есть ли у пользователя (user_id) уже 'taskName' в /users/{userId}.
        2. Если есть — выбрасываем ошибку (или возвращаем).
        3. Если нет — создаём новую задачу, сохраняем её name в Firebase.
        """
        # 1. Получаем данные пользователя
        user_data = self.get_user_data(user_id)
        existing_task_name = user_data.get("taskName")

        if existing_task_name:
            # (Опционально) можно проверить, существует ли задача физически в Cloud Tasks
            # Примерно:
            #    try:
            #        self.client.get_task(name=existing_task_name)
            #        raise ValueError(f"User {user_id} already has a scheduled task: {existing_task_name}")
            #    except NotFound:
            #        pass  # Задача не существует, значит поле устарело
            #
            # Но если мы просто считаем, что наличие taskName -> "уже запланировано", то:
            raise ValueError(f"User {user_id} already has a scheduled task: {existing_task_name}")

        # 2. Создаём новую задачу
        parent = self.client.queue_path(self.project_id, self.location, self.queue_id)
        payload_data = {"userId": user_id}
        payload_bytes = json.dumps(payload_data).encode("utf-8")

        schedule_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=time_answer)
        schedule_time = schedule_time.replace(tzinfo=datetime.timezone.utc)

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.callback_url,
                "headers": {"Content-Type": "application/json"},
                "body": payload_bytes
            },
            "schedule_time": schedule_time,
        }

        created_task = self.client.create_task(parent=parent, task=task)
        task_name = created_task.name
        print(f"[schedule_answer] Created task for user={user_id}: {task_name}")

        # 3. Сохраняем имя задачи в Firebase
        self.save_task_name_in_user(user_id, task_name)
        return task_name

    def get_user_data(self, user_id: str) -> dict:
        """
        Считает запись пользователя из /users/<user_id>.
        Если userId == ключ, то GET /users/{user_id}.json
        Возвращает dict или {}.
        """
        url = f"{self.firebase_base_url}/users/{user_id}.json"
        resp = requests.get(url)
        if resp.status_code != 200:
            print("[get_user_data] Error:", resp.text)
            return {}
        return resp.json() or {}

    def save_task_name_in_user(self, user_id: str, task_name: str):
        """
        Пишем taskName в /users/<user_id>.
        """
        url = f"{self.firebase_base_url}/users/{user_id}.json"
        payload = {"taskName": task_name}
        resp = requests.patch(url, json=payload)
        if resp.status_code >= 300:
            print("[save_task_name_in_user] Error:", resp.text)

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

        self.delete_user_task(user_id)

        print(f"Job completed for userId={user_id}")

    # --- ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ---

    def delete_user_task(self, user_id: str) -> bool:
        """
        1) Читаем /users/{userId}/taskName
        2) Если есть taskName, удаляем её через self.client.delete_task.
        3) Очищаем поле taskName в Firebase.
        Возвращаем True, если что-то удалили.
        """
        # 1. Получаем taskName из Firebase
        user_data = self.get_user_data(user_id)
        task_name = user_data.get("taskName")
        if not task_name:
            print("No taskName in user record.")
            return False

        # 2. Удаляем задачу
        from google.api_core.exceptions import NotFound
        try:
            self.client.delete_task(name=task_name)
            print("Deleted task:", task_name)
        except NotFound:
            print("Task not found:", task_name)

        # 3. Удаляем поле из Firebase
        url = f"{self.firebase_base_url}/users/{user_id}.json"
        payload = {"taskName": None}
        requests.patch(url, json=payload)
        return True

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
