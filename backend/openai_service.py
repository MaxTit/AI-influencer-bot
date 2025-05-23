import os
import openai
import logging
import time
from dotenv import load_dotenv
from typing import List, Dict
from fastapi import HTTPException


# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=30
        )
        self._verify_credentials()
    
    def _verify_credentials(self):
        try:
            self.client.models.list()
        except Exception as e:
            logger.error(f"OpenAI connection failed: {str(e)}")
            raise
        
        
    """Service for working with OpenAI API."""

    def create_thread(self):
        try:
            thread = self.client.beta.threads.create()
            logger.info(f"Created thread {thread.id}")
            return thread.id
        except openai.AuthenticationError as e:
            logger.critical("Authentication failed - check API key")
            raise HTTPException(401, "Invalid API credentials")
        except openai.APIConnectionError as e:
            logger.error("Network connection failed")
            raise HTTPException(503, "API connection failed")
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            raise HTTPException(500, "Internal server error")
        

    def send_prompt(self, thread_id: str, message: str) -> Dict:
        """Send message to assistant and get response."""
        # Add user message to thread
        self.client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=message
        )

        # Create and monitor run
        run = self.client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        while True:
            run_status = self.client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
            logging.info(f"Run status-text: {run_status.status}")
            if run_status.status == "completed":
                break
            if run_status.status == "failed":
                logging.error(f"Run failed: {run_status.last_error}")
                return {"message_id": run.id, "message": "Failed response"}
            time.sleep(1)
        # Get assistant response
        messages = self.client.beta.threads.messages.list(thread_id=thread_id)
        assistant_response = self._parse_assistant_response(messages)
        
        return {"message_id": run.id, "message": assistant_response}

    def send_prompt_with_images(self, thread_id: str, message: str, image_urls: list) -> dict:
        """
        Send a message and images to the assistant and get a response.
        Images are sent as public HTTP(S) URLs in the content blocks.
        """
        # Build content blocks: first the text, then each image
        content_blocks = [{"type": "text", "text": message}]
        for url in image_urls:
            # Ensure url is a string, not a list
            if isinstance(url, list) and len(url) > 0:
                url = url[0]
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": url  # This must be a string!
                }
            })

        # Add user message and images to thread
        self.client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content_blocks
        )

        # Create and monitor run
        run = self.client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        while True:
            run_status = self.client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
            logging.info(f"Run status-images: {run_status.status}")
            if run_status.status == "completed":
                break
            if run_status.status == "failed":
                logging.error(f"Run failed: {run_status.last_error}")
                return {"message_id": run.id, "message": "Failed response"}
            time.sleep(1)

        # Get assistant response
        messages = self.client.beta.threads.messages.list(thread_id=thread_id)
        assistant_response = self._parse_assistant_response(messages)
        return {"message_id": run.id, "message": assistant_response}

    def _parse_assistant_response(self, messages) -> str:
        """Parse the latest assistant response from messages."""
        for msg in messages.data:
            if msg.role == "assistant":
                for content_block in msg.content:
                    if hasattr(content_block, "text") and hasattr(content_block.text, "value"):
                        return content_block.text.value
        return "No response found"

    def get_chat_history(self, thread_id: str) -> List[Dict]:
        """Get chat history for thread."""
        response = self.client.beta.threads.messages.list(thread_id=thread_id)
        return [
            {
                "role": msg.role,
                "content": msg.content[0].text.value,
                "created_at": msg.created_at
            }
            for msg in reversed(response.data)
        ]

    def process_assistant_feedback(self, thread_id: str) -> Dict:
        """Process user feedback and update instructions."""
        history = self.get_chat_history(thread_id)
        corrections = [msg['content'] for msg in history if msg["role"] == "user"]
        
        # Create corrections string separately
        corrections_str = "\n".join(corrections)
        
        user_msg = f'''
        We had an original post with final content:
        {history[-1]["content"]}

        The user made these manual edits:
        {corrections_str}

        Please provide a short summary or instructions update for [New Corrections] in instructions.txt
        Format: Summary: <your summary>"""
        '''
        
        self.send_prompt(thread_id, user_msg)
        message = self.get_last_message(thread_id)
        self.update_instruction_file(message["content"])
        return message

    def get_last_message(self, thread_id: str) -> Dict:
        """Get last message in thread."""
        history = self.get_chat_history(thread_id)
        return history[-1] if history else {}