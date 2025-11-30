from enum import Enum
from typing import List

PRESET_CONFIGS = {
    5:  {"werewolf": 2, "seer": 1, "witch": 0, "hunter": 1, "villager": 1},
    6:  {"werewolf": 2, "seer": 1, "witch": 1, "hunter": 1, "villager": 1},
    7:  {"werewolf": 2, "seer": 1, "witch": 1, "hunter": 1, "villager": 2},
    8:  {"werewolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 2},
    9:  {"werewolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 3}, # 标准局
    10: {"werewolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 4},
}

class GameConfig:
    """游戏配置常量"""
    TOTAL_PLAYERS = 9
    WEREWOLF_COUNT = 3
    SEER_COUNT = 1
    WITCH_COUNT = 1
    HUNTER_COUNT = 1
    VILLAGER_COUNT = 3
    BAN_DURATION_DAYS = 30

    @classmethod
    def get_roles_pool(cls) -> List[str]:
        """获取角色池"""
        return (
            ["werewolf"] * cls.WEREWOLF_COUNT +
            ["seer"] * cls.SEER_COUNT +
            ["witch"] * cls.WITCH_COUNT +
            ["hunter"] * cls.HUNTER_COUNT +
            ["villager"] * cls.VILLAGER_COUNT
        )

class GamePhase(Enum):
    """游戏阶段"""
    WAITING = "等待中"
    NIGHT_WOLF = "夜晚-狼人行动"
    NIGHT_SEER = "夜晚-预言家验人"
    NIGHT_WITCH = "夜晚-女巫行动"
    LAST_WORDS = "遗言阶段"
    DAY_SPEAKING = "白天发言"
    DAY_VOTE = "白天投票"
    DAY_PK = "PK发言"
    FINISHED = "已结束"
