from typing import Optional

class Player:
    def __init__(self, user_id: str, name: str):
        self.user_id = user_id
        self.name = name
        self.role = None
        self.is_alive = True
        self.number = 0

    def __str__(self):
        return f"{self.number}号.{self.name}"
