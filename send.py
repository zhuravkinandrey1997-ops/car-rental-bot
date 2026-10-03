import requests
requests.post("https://api.telegram.org/botТОКЕН/sendDocument", data={"chat_id": "ВАШ_CHAT_ID"}, files={"document": open("clean_bot.zip", "rb")})
