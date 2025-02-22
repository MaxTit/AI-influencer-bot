import os
from dotenv import load_dotenv

load_dotenv()  # This loads the variables from your .env file

# To check a specific variable:
api_key = os.getenv("ASSISTANT_ID")
print("ASSISTANT_ID:", api_key)

# Alternatively, you can check all environment variables:
print(dict(os.environ))
