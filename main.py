from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent

from .werewolf.game_manager import GameManager
from .werewolf.event_handler import EventHandler
from .werewolf.game_config import GameConfig, PRESET_CONFIGS

@register("astrbot_plugin_werewolf", "miao", "狼人杀游戏（3狼3神3平民+AI复盘）", "v1.0.0")
class WerewolfPlugin(Star):
    def __init__(self, context: Context, config: dict = None, *args, **kwargs):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        self.game_manager = GameManager()
        self.event_handler = EventHandler(self.game_manager, self.context)

        self._apply_config()

        role_sum = (GameConfig.WEREWOLF_COUNT + GameConfig.SEER_COUNT +
                   GameConfig.WITCH_COUNT + GameConfig.HUNTER_COUNT +
                   GameConfig.VILLAGER_COUNT)
        if role_sum != GameConfig.TOTAL_PLAYERS:
            logger.warning(f"[狼人杀] 角色配置不匹配！恢复默认9人局配置。")
            self._apply_config(default=True)

        ai_status = "已关闭" if not self.game_manager.enable_ai_review else (
            f"{self.game_manager.ai_review_model or '默认模型'}"
            f"{' (自定义提示词)' if self.game_manager.ai_review_prompt else ''}"
        )
        logger.info(
            f"[狼人杀] 插件已加载 | "
            f"游戏配置：{GameConfig.TOTAL_PLAYERS}人局"
            f"({GameConfig.WEREWOLF_COUNT}狼{GameConfig.SEER_COUNT+GameConfig.WITCH_COUNT+GameConfig.HUNTER_COUNT}神{GameConfig.VILLAGER_COUNT}民) | "
            f"AI复盘：{ai_status}"
        )

    def _apply_config(self, default=False):
        if default:
            self.config = {} # 清空配置以使用默认值
        
        total_players = self.config.get("total_players", 9)
        if total_players not in PRESET_CONFIGS:
            total_players = 9
        
        default_roles = PRESET_CONFIGS[total_players]
        GameConfig.TOTAL_PLAYERS = total_players
        GameConfig.WEREWOLF_COUNT = self.config.get("werewolf_count", default_roles['werewolf'])
        GameConfig.SEER_COUNT = self.config.get("seer_count", default_roles['seer'])
        GameConfig.WITCH_COUNT = self.config.get("witch_count", default_roles['witch'])
        GameConfig.HUNTER_COUNT = self.config.get("hunter_count", default_roles['hunter'])
        GameConfig.VILLAGER_COUNT = self.config.get("villager_count", default_roles['villager'])
        GameConfig.BAN_DURATION_DAYS = self.config.get("ban_duration_days", 30)

        self.game_manager.timeouts = {
            "wolf": self.config.get("timeout_wolf", 120),
            "seer": self.config.get("timeout_seer", 120),
            "witch": self.config.get("timeout_witch", 120),
            "hunter": self.config.get("timeout_hunter", 120),
            "speaking": self.config.get("timeout_speaking", 120),
            "vote": self.config.get("timeout_vote", 120),
            "dead_min": self.config.get("timeout_dead_min", 10),
            "dead_max": self.config.get("timeout_dead_max", 15),
        }
        self.game_manager.enable_ai_review = self.config.get("enable_ai_review", True)
        self.game_manager.ai_review_model = self.config.get("ai_review_model", "")
        self.game_manager.ai_review_prompt = self.config.get("ai_review_prompt", "")

    @filter.command("创建房间")
    async def create_room(self, event: AstrMessageEvent, player_count: int = 9):
        result = await self.event_handler.create_room(event, player_count)
        if result:
            yield event.plain_result(result)

    @filter.command("解散房间")
    async def dismiss_room(self, event: AstrMessageEvent):
        result = await self.event_handler.dismiss_room(event)
        if result:
            yield event.plain_result(result)

    @filter.command("加入房间")
    async def join_room(self, event: AstrMessageEvent):
        result = await self.event_handler.join_room(event)
        if result:
            yield event.plain_result(result)

    @filter.command("开始游戏")
    async def start_game(self, event: AstrMessageEvent):
        result = await self.event_handler.start_game(event)
        if result:
            yield event.plain_result(result)

    @filter.command("查角色")
    async def check_role(self, event: AstrMessageEvent):
        result = await self.event_handler.check_role(event)
        if result:
            yield event.plain_result(result)

    @filter.command("游戏状态")
    async def show_status(self, event: AstrMessageEvent):
        result = await self.event_handler.show_status(event)
        if result:
            yield event.plain_result(result)

    @filter.command("结束游戏")
    async def end_game(self, event: AstrMessageEvent):
        result = await self.event_handler.end_game(event)
        if result:
            yield event.plain_result(result)

    @filter.command("办掉")
    async def werewolf_kill(self, event: AstrMessageEvent):
        result = await self.event_handler.werewolf_kill(event)
        if result:
            yield event.plain_result(result)
    
    @filter.command("密谋")
    async def werewolf_chat(self, event: AstrMessageEvent):
        result = await self.event_handler.werewolf_chat(event)
        if result:
            yield event.plain_result(result)

    @filter.command("验人")
    async def seer_check(self, event: AstrMessageEvent):
        result = await self.event_handler.seer_check(event)
        if result:
            yield event.plain_result(result)

    @filter.command("救人")
    async def witch_save(self, event: AstrMessageEvent):
        result = await self.event_handler.witch_save(event)
        if result:
            yield event.plain_result(result)

    @filter.command("毒人")
    async def witch_poison(self, event: AstrMessageEvent):
        result = await self.event_handler.witch_poison(event)
        if result:
            yield event.plain_result(result)

    @filter.command("不操作")
    async def witch_pass(self, event: AstrMessageEvent):
        result = await self.event_handler.witch_pass(event)
        if result:
            yield event.plain_result(result)

    @filter.command("遗言完毕")
    async def finish_last_words(self, event: AstrMessageEvent):
        result = await self.event_handler.finish_last_words(event)
        if result:
            yield event.plain_result(result)

    @filter.command("发言完毕")
    async def finish_speaking(self, event: AstrMessageEvent):
        result = await self.event_handler.finish_speaking(event)
        if result:
            yield event.plain_result(result)

    @filter.command("开始投票")
    async def start_vote(self, event: AstrMessageEvent):
        result = await self.event_handler.start_vote(event)
        if result:
            yield event.plain_result(result)

    @filter.command("投票")
    async def day_vote(self, event: AstrMessageEvent):
        result = await self.event_handler.day_vote(event)
        if result:
            yield event.plain_result(result)

    @filter.command("开枪")
    async def hunter_shoot(self, event: AstrMessageEvent):
        result = await self.event_handler.hunter_shoot(event)
        if result:
            yield event.plain_result(result)

    @filter.command("狼人杀帮助")
    async def show_help(self, event: AstrMessageEvent):
        result = self.event_handler.show_help(event)
        if result:
            yield event.plain_result(result)
        
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def capture_speech(self, event: AstrMessageEvent):
        await self.event_handler.capture_speech(event)

    async def terminate(self):
        logger.info("狼人杀插件已终止")
