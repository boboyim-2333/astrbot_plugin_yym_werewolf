from typing import Dict, Optional

from .game import Game

class GameManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(GameManager, cls).__new__(cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        self.games: Dict[str, Game] = {}
        self.timeouts: dict = {}
        self.enable_ai_review: bool = True
        self.ai_review_model: str = ""
        self.ai_review_prompt: str = ""

    def create_game(self, group_id: str, creator_id: str, bot, msg_origin, player_count: int, context) -> Game:
        if group_id in self.games:
            raise ValueError("该群组已存在一个游戏房间")
        
        game = Game(
            group_id, creator_id, bot, msg_origin, player_count, context,
            self.timeouts, self.enable_ai_review, self.ai_review_model, self.ai_review_prompt
        )
        self.games[group_id] = game
        return game

    def get_game(self, group_id: str) -> Optional[Game]:
        return self.games.get(group_id)

    def get_game_by_player(self, player_id: str) -> Optional[Game]:
        for game in self.games.values():
            if player_id in game.players:
                return game
        return None

    async def remove_game(self, group_id: str):
        game = self.get_game(group_id)
        if game:
            await game.cleanup()
            if group_id in self.games:
                del self.games[group_id]

    async def end_game_cleanup(self, group_id: str, winning_faction: str):
        game = self.get_game(group_id)
        if game:
            await game.end_game_cleanup(winning_faction)
            await self.remove_game(group_id)
