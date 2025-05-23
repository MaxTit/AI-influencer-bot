# scheduler_service.py

import os
import json
import time
import datetime
import requests
import base64
from typing import Dict, Optional
from google.cloud import tasks_v2  # pip install google-cloud-tasks
from google.api_core.exceptions import AlreadyExists, NotFound
from google.protobuf import field_mask_pb2
from urllib.parse import quote, unquote
from google.cloud import storage
import re
import logging
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
        
        logging.basicConfig(level=logging.INFO)

        self.client = tasks_v2.CloudTasksClient()
        self.firebase_base_url = "https://relationship-with-iryn-default-rtdb.firebaseio.com"
        self.webhook_tg_bot_url = "https://ai-influencer-tg-bot-702470178997.us-central1.run.app/answer"

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
            logging.info(f"User {user_id} already has a scheduled task: {existing_task_name}")
            return existing_task_name

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
        7. Удаляет изображения из Cloud Storage.
        8. Удаляет задачу из Cloud Tasks.
        9. Отправляет ответ в Telegram.
        """
        logging.info(f"[run_answer_job] for userId={user_id}")

        # 1. Собираем сообщения
        messages_data = self.fetch_user_messages(user_id)
        if not messages_data:
            logging.info("No messages found in /Messages or all answered.")
            return

        pending_msgs = sorted(
            (m for m in messages_data if m.get("isAnswer") == False),
            key=lambda x: x.get("dateSend", 0)
        )
        if not pending_msgs:
            logging.info("No messages with isAnswer=false.")
            return

        # 2. Собираем текст
        # Markdown-цитаты
        combined_text = "\n\n".join(f"> {msg['message']}" for msg in pending_msgs)
        image_urls = [msg.get("pictureUrl") for msg in pending_msgs if msg.get("pictureUrl")]

        # 3. Получаем aiTreadId
        logging.info(f"[run_answer_job] Fetching aiTreadId for userId={user_id}")
        ai_thread_id = self.fetch_user_ai_thread_id(user_id)
        if not ai_thread_id:
            logging.info(f"No aiTreadId found for userId={user_id}")
            return
        
        # 4. Отправляем запрос в OpenAI
        logging.info(f"[run_answer_job] Sending prompt to OpenAI with {len(image_urls)} images")
        if len(image_urls) > 0:
            response = self.openai_service.send_prompt_with_images(ai_thread_id, combined_text, image_urls)
        else:
            response = self.openai_service.send_prompt(ai_thread_id, combined_text)
        assistant_message = response.get("message", "No response")

        # 5. Записываем ответ
        logging.info(f"[run_answer_job] Storing bot message for userId={user_id}")
        self.store_bot_message(user_id, assistant_message)

        # 6. Помечаем старые сообщения как isAnswer=true
        logging.info(f"[run_answer_job] Marking messages as answered for userId={user_id}")
        self.mark_messages_answered(pending_msgs)

        #logging.info(f"[run_answer_job] Deleting images for userId={user_id}")
        #self.delete_images(image_urls)

        logging.info(f"[run_answer_job] Deleting user task for userId={user_id}")
        self.delete_user_task(user_id)

        # 7. Отправляем ответ в Telegram
        if user_id.startswith("tg_"):
            logging.info(f"[run_answer_job] Sending answer to Telegram for userId={user_id}")
            self.send_answer_to_telegram(user_id, assistant_message)

        logging.info(f"Job completed for userId={user_id}")

    def send_answer_to_telegram(self, user_id: str, message: str):
        """
        Sends the assistant's response message to the Telegram bot webhook.
        
        Args:
            user_id (str): The Telegram user ID to send the message to
            message (str): The message text to send
        """
        payload = {
            "user_id": user_id,
            "message": message
        }
        
        try:
            response = requests.post(
                self.webhook_tg_bot_url,
                params=payload
            )
            
            if response.status_code >= 300:
                print(f"[send_answer_to_telegram] Error sending message to Telegram. Status: {response.status_code}, Response: {response.text}")
            else:
                print(f"[send_answer_to_telegram] Successfully sent message to user {user_id}")
                
        except Exception as e:
            print(f"[send_answer_to_telegram] Exception while sending message: {str(e)}")

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

    def url_to_base64(self, image_url):
        try:
            # Handle case where image_url is passed as a list
            if isinstance(image_url, list) and len(image_url) > 0:
                image_url = image_url[0]

            # Remove leading '@' if present
            if isinstance(image_url, str) and image_url.startswith('@'):
                image_url = image_url[1:]

            # Handle Firebase Storage gs:// URLs
            if isinstance(image_url, str) and image_url.startswith('gs://'):
                parts = image_url[5:].split('/', 1)
                bucket = parts[0].replace('.firebasestorage.app', '.appspot.com')
                object_path = parts[1]
                object_path_enc = quote(object_path, safe='')
                image_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{object_path_enc}?alt=media"
                print(f"[url_to_base64] Converted gs:// to: {image_url}")

            # If it's already a direct Firebase Storage URL, use as is
            # (no conversion needed)

            response = requests.get(image_url)
            if response.status_code == 200:
                return base64.b64encode(response.content).decode('utf-8')
            else:
                print(f"[url_to_base64] Failed to download image. Status: {response.status_code}, URL: {image_url}")
                print(f"[url_to_base64] Response: {response.text}")
                return None
        except Exception as e:
            print(f"[url_to_base64] Exception for {image_url}: {str(e)}")
            return None

    def delete_images(self, image_urls):
        """
        Deletes images from Google Cloud Storage given a list of gs:// or public Firebase Storage URLs.
        """
        storage_client = storage.Client()
        for url in image_urls:
            # If url is a list, get the first element
            if isinstance(url, list) and len(url) > 0:
                url = url[0]
            # Handle @ prefix
            if isinstance(url, str) and url.startswith('@'):
                url = url[1:]
            # gs:// URL
            if isinstance(url, str) and url.startswith('gs://'):
                parts = url[5:].split('/', 1)
                bucket_name = parts[0]
                blob_name = parts[1]
            # Public Firebase Storage URL with firebasestorage.googleapis.com
            elif isinstance(url, str) and 'firebasestorage.googleapis.com' in url:
                match = re.search(r'/b/([^/]+)/o/([^?]+)', url)
                if match:
                    bucket_name = match.group(1)
                    blob_name = unquote(match.group(2))
                else:
                    print(f"[delete_images] Could not parse public URL: {url}")
                    continue
            # storage.googleapis.com URL format
            elif isinstance(url, str) and 'storage.googleapis.com' in url:
                # Extract bucket and path from URL like:
                # https://storage.googleapis.com/relationship-with-iryn.firebasestorage.app/348409461/...
                parts = url.split('storage.googleapis.com/', 1)
                if len(parts) == 2:
                    bucket_path = parts[1].split('/', 1)
                    if len(bucket_path) == 2:
                        bucket_name = bucket_path[0]
                        blob_name = bucket_path[1]
                    else:
                        print(f"[delete_images] Could not parse storage.googleapis.com URL path: {url}")
                        continue
                else:
                    print(f"[delete_images] Could not parse storage.googleapis.com URL: {url}")
                    continue
            else:
                print(f"[delete_images] Skipped non-gs/non-public URL: {url}")
                continue
            # Try to delete
            try:
                bucket = storage_client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                blob.delete()
                print(f"[delete_images] Deleted: {url}")
            except Exception as e:
                print(f"[delete_images] Failed to delete {url}: {e}")
