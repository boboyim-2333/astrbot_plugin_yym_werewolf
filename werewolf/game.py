import asyncio
import random
from typing import Dict, Set, List, Optional

from astrbot.api.star import Context
from astrbot.core.message.components import At
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api import logger

from .game_config import GamePhase, PRESET_CONFIGS, GameConfig
from .player import Player
from .role import get_role_by_name

LOG_SEPARATOR = "=" * 30

class Game:
    def __init__(self, group_id: str, creator_id: str, bot, msg_origin, player_count: int, context: Context, timeouts: dict, enable_ai_review: bool, ai_review_model: str, ai_review_prompt: str):
        self.group_id = group_id
        self.creator_id = creator_id
        self.bot = bot
        self.msg_origin = msg_origin
        self.context = context
        self.timeouts = timeouts
        self.enable_ai_review = enable_ai_review
        self.ai_review_model = ai_review_model
        self.ai_review_prompt = ai_review_prompt

        config = PRESET_CONFIGS[player_count]
        self.config = {
            "total": player_count,
            "werewolf": config["werewolf"],
            "seer": config["seer"],
            "witch": config["witch"],
            "hunter": config["hunter"],
            "villager": config["villager"]
        }

        self.players: Dict[str, Player] = {}
        self.phase = GamePhase.WAITING
        self.night_votes: Dict[str, str] = {}
        self.day_votes: Dict[str, str] = {}
        self.night_result: Optional[str] = None
        self.seer_checked = False
        self.banned_players: Set[str] = set()
        self.timer_task: Optional[asyncio.Task] = None
        self.speaking_order: List[str] = []
        self.current_speaker_index = 0
        self.current_speaker: Optional[str] = None
        self.temp_admins: Set[str] = set()
        self.last_killed: Optional[str] = None
        self.witch_poison_used = False
        self.witch_antidote_used = False
        self.witch_saved: Optional[str] = None
        self.witch_poisoned: Optional[str] = None
        self.witch_acted = False
        self.is_first_night = True
        self.last_words_from_vote = False
        self.pk_players: List[str] = []
        self.is_pk_vote = False
        self.number_to_player: Dict[int, str] = {}
        self.original_group_cards: Dict[str, str] = {}
        self.hunter_shot = False
        self.pending_hunter_shot: Optional[str] = None
        self.hunter_death_type: Optional[str] = None
        self.game_log: List[str] = []
        self.current_round = 0
        self.current_speech: List[str] = []

    def add_player(self, player: Player):
        self.players[player.user_id] = player

    def get_player(self, user_id: str) -> Optional[Player]:
        return self.players.get(user_id)

    @property
    def alive_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.is_alive]
    
    @property
    def alive_player_ids(self) -> List[str]:
        return [p.user_id for p in self.players.values() if p.is_alive]

    def start(self):
        players_list = list(self.players.values())
        random.shuffle(players_list)

        for index, player in enumerate(players_list, start=1):
            player.number = index
            self.number_to_player[index] = player.user_id

        roles_pool = (
            ["werewolf"] * self.config["werewolf"] +
            ["seer"] * self.config["seer"] +
            ["witch"] * self.config["witch"] +
            ["hunter"] * self.config["hunter"] +
            ["villager"] * self.config["villager"]
        )
        random.shuffle(roles_pool)

        for player, role_name in zip(players_list, roles_pool):
            player.role = get_role_by_name(role_name)

        self.phase = GamePhase.NIGHT_WOLF
        self.current_round = 1

        self.game_log.append(LOG_SEPARATOR)
        self.game_log.append("第1晚")
        self.game_log.append(LOG_SEPARATOR)

    def _format_player_name(self, player_id: str) -> str:
        player = self.get_player(player_id)
        return str(player) if player else "未知"

    def _parse_target(self, target_str: str) -> Optional[str]:
        try:
            number = int(target_str)
            if number in self.number_to_player:
                return self.number_to_player[number]
        except (ValueError, TypeError):
            pass

        if target_str in self.players:
            return target_str

        return None

    async def _set_group_cards_to_numbers(self):
        for player_id, player in self.players.items():
            try:
                if player_id not in self.original_group_cards:
                    self.original_group_cards[player_id] = player.name

                new_card = f"{player.number}号"
                await self.bot.set_group_card(group_id=int(self.group_id), user_id=int(player_id), card=new_card)
                logger.info(f"[狼人杀] 已将玩家 {player_id} 群昵称改为 {new_card}")
            except Exception as e:
                logger.error(f"[狼人杀] 修改玩家 {player_id} 群昵称失败: {e}")

    async def _restore_group_cards(self):
        for player_id, original_card in self.original_group_cards.items():
            try:
                await self.bot.set_group_card(group_id=int(self.group_id), user_id=int(player_id), card=original_card)
                logger.info(f"[狼人杀] 已恢复玩家 {player_id} 群昵称为 {original_card}")
            except Exception as e:
                logger.error(f"[狼人杀] 恢复玩家 {player_id} 群昵称失败: {e}")

    async def cleanup(self):
        await self._restore_group_cards()
        await self._cancel_timer()
        await self._unban_all_players()
        await self._set_group_whole_ban(False)
        await self._clear_temp_admins()
        logger.info(f"[狼人杀] 群 {self.group_id} 房间已清理")

    def _get_all_players_roles(self) -> str:
        result = "📜 身份公布：\n\n"
        roles_map = {"werewolf": [], "seer": [], "witch": [], "hunter": [], "villager": []}
        
        for player in self.players.values():
            if player.role and player.role.name in roles_map:
                roles_map[player.role.name].append(self._format_player_name(player.user_id))

        if roles_map["werewolf"]:
            result += "🐺 狼人：\n" + "\n".join([f"  • {name}" for name in roles_map["werewolf"]]) + "\n\n"
        if roles_map["seer"]:
            result += "🔮 预言家：\n" + "\n".join([f"  • {name}" for name in roles_map["seer"]]) + "\n\n"
        if roles_map["witch"]:
            result += "💊 女巫：\n" + "\n".join([f"  • {name}" for name in roles_map["witch"]]) + "\n\n"
        if roles_map["hunter"]:
            result += "🔫 猎人：\n" + "\n".join([f"  • {name}" for name in roles_map["hunter"]]) + "\n\n"
        if roles_map["villager"]:
            result += "👤 平民：\n" + "\n".join([f"  • {name}" for name in roles_map["villager"]])

        return result

    async def _ban_player(self, player_id: str):
        try:
            await self.bot.set_group_ban(group_id=int(self.group_id), user_id=int(player_id), duration=86400 * GameConfig.BAN_DURATION_DAYS)
            self.banned_players.add(player_id)
            logger.info(f"[狼人杀] 已禁言玩家 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 禁言玩家 {player_id} 失败: {e}")

    async def _unban_all_players(self):
        for player_id in self.banned_players:
            try:
                await self.bot.set_group_ban(group_id=int(self.group_id), user_id=int(player_id), duration=0)
                logger.info(f"[狼人杀] 已解除禁言 {player_id}")
            except Exception as e:
                logger.error(f"[狼人杀] 解除禁言 {player_id} 失败: {e}")
        self.banned_players.clear()

    async def _set_group_whole_ban(self, enable: bool):
        try:
            await self.bot.set_group_whole_ban(group_id=int(self.group_id), enable=enable)
            logger.info(f"[狼人杀] 全员禁言状态: {enable}")
        except Exception as e:
            logger.error(f"[狼人杀] 设置全员禁言失败: {e}")

    async def _set_temp_admin(self, player_id: str):
        try:
            await self.bot.set_group_admin(group_id=int(self.group_id), user_id=int(player_id), enable=True)
            self.temp_admins.add(player_id)
            logger.info(f"[狼人杀] 已设置临时管理员 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 设置临时管理员 {player_id} 失败: {e}")

    async def _remove_temp_admin(self, player_id: str):
        try:
            await self.bot.set_group_admin(group_id=int(self.group_id), user_id=int(player_id), enable=False)
            self.temp_admins.discard(player_id)
            logger.info(f"[狼人杀] 已取消临时管理员 {player_id}")
        except Exception as e:
            logger.error(f"[狼人杀] 取消临时管理员 {player_id} 失败: {e}")

    async def _clear_temp_admins(self):
        for player_id in list(self.temp_admins):
            await self._remove_temp_admin(player_id)
        self.temp_admins.clear()

    async def _send_roles_to_players(self):
        for player in self.players.values():
            try:
                role_text = self._get_role_info_text(player)
                await self.bot.send_private_msg(user_id=int(player.user_id), message=role_text)
                logger.info(f"[狼人杀] 已私聊告知玩家 {player.user_id} 的身份：{player.role.name}")
            except Exception as e:
                logger.warning(f"[狼人杀] 私聊告知玩家 {player.user_id} 失败: {e}")

    def _get_role_info_text(self, player: Player) -> str:
        role = player.role
        player_id = player.user_id
        
        if role.name == "werewolf":
            teammates = [p for p in self.players.values() if p.role.name == "werewolf" and p.user_id != player_id]
            teammate_info = f"\n\n🤝 你的队友：{', '.join([self._format_player_name(p.user_id) for p in teammates])}" if teammates else ""
            other_players = [p for p in self.players.values() if p.role.name != "werewolf"]
            players_list = "\n".join([f"  • {self._format_player_name(p.user_id)}" for p in other_players])
            return (f"🎭 游戏开始！你的角色是：\n\n🐺 狼人\n\n你的目标：消灭所有平民！{teammate_info}\n\n"
                    f"📋 可选目标列表：\n{players_list}\n\n💡 夜晚私聊使用命令：\n  /办掉 编号\n  /密谋 消息")
        elif role.name == "seer":
            other_players = [p for p in self.players.values() if p.user_id != player_id]
            players_list = "\n".join([f"  • {self._format_player_name(p.user_id)}" for p in other_players])
            return (f"🎭 游戏开始！你的角色是：\n\n🔮 预言家\n\n你的目标：找出狼人，帮助平民获胜！\n\n"
                    f"📋 可验证玩家列表：\n{players_list}\n\n💡 夜晚私聊使用命令：\n/验人 编号")
        elif role.name == "witch":
            return (f"🎭 游戏开始！你的角色是：\n\n💊 女巫\n\n你的目标：帮助平民获胜！\n\n你拥有两种药：\n"
                    f"💉 解药：可以救活当晚被杀的人（只能用一次）\n💊 毒药：可以毒杀任何人（只能用一次）\n\n"
                    f"💡 夜晚私聊使用命令：\n  /救人\n  /毒人 编号\n  /不操作")
        elif role.name == "hunter":
            other_players = [p for p in self.players.values() if p.user_id != player_id]
            players_list = "\n".join([f"  • {self._format_player_name(p.user_id)}" for p in other_players])
            return (f"🎭 游戏开始！你的角色是：\n\n🔫 猎人\n\n你的目标：帮助好人获胜！\n\n你的技能：\n"
                    f"• 被狼人办掉或被投票放逐时可以开枪带走一人\n• 被女巫毒死时不能开枪\n\n"
                    f"📋 可选目标列表：\n{players_list}\n\n💡 当你死亡时（非毒死），私聊使用命令：\n  /开枪 编号")
        else:
            return (f"🎭 游戏开始！你的角色是：\n\n👤 平民\n\n你的目标：找出并放逐所有狼人！\n"
                    f"白天投票时使用 /投票 编号 放逐可疑玩家。")

    def check_victory_condition(self) -> tuple:
        alive_werewolves = sum(1 for p in self.alive_players if p.role.name == "werewolf")
        alive_goods = len(self.alive_players) - alive_werewolves
        alive_gods = sum(1 for p in self.alive_players if p.role.name in ["seer", "witch", "hunter"])

        if alive_werewolves == 0:
            return ("好人胜利！所有狼人已被放逐！", "villager")
        elif alive_goods <= alive_werewolves:
            return ("狼人胜利！好人数量不足！", "werewolf")
        elif len(alive_gods) == 0 and alive_werewolves > 0:
            return ("狼人胜利！所有神职人员已出局！", "werewolf")
        else:
            return ("", None)

    async def _cancel_timer(self):
        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()
        self.timer_task = None

    async def _process_night_kill(self):
        if not self.night_votes:
            self.game_log.append(f"🌙 狼人未采取行动")
            return

        vote_counts = {}
        for target in self.night_votes.values():
            vote_counts[target] = vote_counts.get(target, 0) + 1

        max_votes = max(vote_counts.values())
        targets = [pid for pid, count in vote_counts.items() if count == max_votes]
        killed_player_id = random.choice(targets)
        
        self.night_votes = {}
        self.last_killed = killed_player_id
        
        killed_name = self._format_player_name(killed_player_id)
        self.game_log.append(f"🌙 狼人最终决定刀 {killed_name}")

    async def _process_day_vote(self):
        valid_votes = [t for t in self.day_votes.values() if t != "ABSTAIN"]
        abstain_count = len(self.day_votes) - len(valid_votes)

        if not valid_votes:
            await self._enter_night_without_death(f"{abstain_count}人弃票")
            return

        vote_counts = {}
        for target in valid_votes:
            vote_counts[target] = vote_counts.get(target, 0) + 1

        max_votes = max(vote_counts.values())
        targets = [pid for pid, count in vote_counts.items() if count == max_votes]

        if len(targets) > 1 and not self.is_pk_vote:
            self.pk_players = sorted(targets, key=lambda pid: self.get_player(pid).number)
            self.phase = GamePhase.DAY_PK
            self.day_votes = {}
            self.current_speaker_index = 0
            
            pk_names = [self._format_player_name(pid) for pid in self.pk_players]
            result_text = (
                f"\n📊 投票结果公布！\n\n"
                f"⚠️ 出现平票！以下玩家票数相同：\n"
                + "\n".join([f"  • {name}" for name in pk_names])
                + f"\n\n进入PK环节！\n平票玩家将依次发言（每人2分钟），然后进行二次投票。\n"
            )
            await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
            await self._set_group_whole_ban(True)
            await self._next_pk_speaker()
            return

        if len(targets) > 1 and self.is_pk_vote:
            await self._enter_night_without_death("PK再次平票")
            return
        
        exiled_player_id = targets[0]
        exiled_player = self.get_player(exiled_player_id)
        exiled_player.is_alive = False
        
        self.is_pk_vote = False
        self.pk_players = []
        self.day_votes = {}
        self.last_killed = exiled_player_id
        
        exiled_name = self._format_player_name(exiled_player_id)
        self.game_log.append(f"📊 投票结果：{exiled_name} 被放逐")
        
        result_text = (
            f"\n📊 投票结果公布！\n\n"
            + f"玩家 {exiled_name} 被放逐了！\n\n"
            + f"存活玩家：{len(self.alive_players)}/{len(self.players)}\n\n"
        )
        
        if exiled_player.role.name == "hunter":
            self.pending_hunter_shot = exiled_player_id
            self.hunter_death_type = "vote"
            await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
            try:
                msg = (f"💀 你被投票放逐了！\n\n🔫 你可以选择开枪带走一个人！\n\n"
                       f"请私聊使用命令：\n  /开枪 编号\n⏰ 限时{self.timeouts['hunter']}秒")
                await self.bot.send_private_msg(user_id=int(exiled_player_id), message=msg)
                group_msg = f"⚠️ {exiled_name} 是猎人，可以选择开枪带走一个人..."
                await self.context.send_message(self.msg_origin, MessageChain().message(group_msg))
                self.timer_task = asyncio.create_task(self._hunter_shot_timeout_for_vote())
                return
            except Exception as e:
                logger.error(f"[狼人杀] 通知猎人 {exiled_player_id} 开枪失败: {e}")

        victory_msg, winning_faction = self.check_victory_condition()
        if victory_msg:
            result_text += f"🎉 {victory_msg}\n游戏结束！\n\n" + self._get_all_players_roles()
            self.phase = GamePhase.FINISHED
            await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
            await self.end_game_cleanup(winning_faction)
        else:
            self.phase = GamePhase.LAST_WORDS
            self.last_words_from_vote = True
            await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
            await self._start_last_words()

    async def _enter_night_without_death(self, reason: str):
        self.game_log.append(f"📊 结果：{reason}，本轮无人出局")
        self.is_pk_vote = False
        self.pk_players = []
        self.day_votes = {}
        
        self.phase = GamePhase.NIGHT_WOLF
        self.seer_checked = False
        self.is_first_night = False
        self.current_round += 1
        
        self.game_log.extend([LOG_SEPARATOR, f"第{self.current_round}晚", LOG_SEPARATOR])
        
        await self._set_group_whole_ban(True)
        msg = MessageChain().message(
            f"📊 {reason}，本轮无人出局！\n\n"
            "🌙 夜晚降临，天黑请闭眼...\n"
            "🐺 狼人请私聊 /办掉 编号\n"
            "🔮 预言家请等待\n"
            f"⏰ 剩余时间：{self.timeouts['wolf']}秒"
        )
        await self.context.send_message(self.msg_origin, msg)
        self.timer_task = asyncio.create_task(self._wolf_kill_timeout())

    async def _start_last_words(self):
        if not self.last_killed:
            self.phase = GamePhase.DAY_SPEAKING
            await self._start_speaking_phase()
            return

        self.current_speech = []
        await self._set_group_whole_ban(True)
        await self._set_temp_admin(self.last_killed)

        killed_name = self._format_player_name(self.last_killed)
        msg = MessageChain().at(self.get_player(self.last_killed).name, self.last_killed).message(
            f" 现在请你留遗言\n\n"
            f"⏰ 遗言时间：{self.timeouts['speaking']}秒\n"
            f"💡 遗言完毕后请使用：/遗言完毕"
        )
        await self.context.send_message(self.msg_origin, msg)
        self.timer_task = asyncio.create_task(self._last_words_timeout())

    async def _start_speaking_phase(self):
        self.speaking_order = sorted(self.alive_player_ids, key=lambda pid: self.get_player(pid).number)
        self.current_speaker_index = 0
        await self._set_group_whole_ban(True)
        await self._next_speaker()

    async def _next_speaker(self):
        if self.current_speaker_index >= len(self.speaking_order):
            await self._auto_start_vote()
            return

        self.current_speaker = self.speaking_order[self.current_speaker_index]
        self.current_speech = []
        await self._set_temp_admin(self.current_speaker)

        speaker_name = self._format_player_name(self.current_speaker)
        speaker_player = self.get_player(self.current_speaker)
        msg = MessageChain().at(speaker_player.name, speaker_player.user_id).message(
            f" 现在轮到你发言\n\n"
            f"⏰ 发言时间：{self.timeouts['speaking']}秒\n"
            f"💡 发言完毕后请使用：/发言完毕\n\n"
            f"进度：{self.current_speaker_index + 1}/{len(self.speaking_order)}"
        )
        await self.context.send_message(self.msg_origin, msg)
        self.timer_task = asyncio.create_task(self._speaking_timeout())

    async def _next_pk_speaker(self):
        if self.current_speaker_index >= len(self.pk_players):
            await self._start_pk_vote()
            return

        self.current_speaker = self.pk_players[self.current_speaker_index]
        self.current_speech = []
        await self._set_temp_admin(self.current_speaker)

        speaker_name = self._format_player_name(self.current_speaker)
        speaker_player = self.get_player(self.current_speaker)
        msg = MessageChain().at(speaker_player.name, speaker_player.user_id).message(
            f" PK发言：现在轮到你发言\n\n"
            f"⏰ 发言时间：{self.timeouts['speaking']}秒\n"
            f"💡 发言完毕后请使用：/发言完毕\n\n"
            f"进度：{self.current_speaker_index + 1}/{len(self.pk_players)}"
        )
        await self.context.send_message(self.msg_origin, msg)
        self.timer_task = asyncio.create_task(self._pk_speaking_timeout())
    
    async def _start_pk_vote(self):
        self.phase = GamePhase.DAY_VOTE
        self.is_pk_vote = True
        self.day_votes = {}

        pk_names = [self._format_player_name(pid) for pid in self.pk_players]
        msg = MessageChain().message(
            "📢 PK发言完毕！现在开始二次投票\n\n"
            "⚠️ 只能投给以下平票玩家：\n"
            + "\n".join([f"  • {name}" for name in pk_names])
            + f"\n\n⏰ 投票时间：{self.timeouts['vote']}秒\n"
            + "💡 使用 /投票 编号"
        )
        await self.context.send_message(self.msg_origin, msg)
        await self._set_group_whole_ban(False)
        self.timer_task = asyncio.create_task(self._day_vote_timeout())

    async def _auto_start_vote(self):
        self.phase = GamePhase.DAY_VOTE
        self.day_votes = {}
        
        vote_msg = MessageChain().message(
            "📊 发言环节结束！现在进入投票阶段！\n\n"
            "请所有存活玩家使用命令：\n"
            "/投票 编号\n\n"
            f"当前存活人数：{len(self.alive_players)}\n"
            f"⏰ 剩余时间：{self.timeouts['vote']}秒"
        )
        await self.context.send_message(self.msg_origin, vote_msg)
        await self._set_group_whole_ban(False)
        self.timer_task = asyncio.create_task(self._day_vote_timeout())

    async def _notify_witch(self, witch_id: str):
        try:
            if not self.last_killed:
                msg = (f"💊 女巫行动阶段\n\n今晚没有人被杀！\n\n"
                       f"💊 毒药状态：{'已使用' if self.witch_poison_used else '可用'}\n"
                       f"💉 解药状态：{'已使用' if self.witch_antidote_used else '可用'}\n\n"
                       "命令：\n  /毒人 编号\n  /不操作")
            else:
                killed_name = self._format_player_name(self.last_killed)
                msg = (f"💊 女巫行动阶段\n\n今晚被杀的是：{killed_name}\n\n"
                       f"💊 毒药状态：{'已使用' if self.witch_poison_used else '可用'}\n"
                       f"💉 解药状态：{'已使用' if self.witch_antidote_used else '可用'}\n\n"
                       "命令：\n  /救人\n  /毒人 编号\n  /不操作")

            await self.bot.send_private_msg(user_id=int(witch_id), message=msg)
            logger.info(f"[狼人杀] 已告知女巫 {witch_id} 夜晚信息")
        except Exception as e:
            logger.error(f"[狼人杀] 告知女巫 {witch_id} 失败: {e}")

    async def _witch_finish(self):
        # 1. 处理救人
        if self.witch_saved:
            self.last_killed = None
        elif self.last_killed:
            self.get_player(self.last_killed).is_alive = False

        # 2. 处理毒人
        if self.witch_poisoned:
            self.get_player(self.witch_poisoned).is_alive = False
            await self._ban_player(self.witch_poisoned)
            if self.get_player(self.witch_poisoned).role.name == 'hunter':
                self.hunter_death_type = "poison"

        # 3. 处理被狼杀的是否是猎人
        if self.last_killed and not self.witch_saved:
            if self.get_player(self.last_killed).role.name == 'hunter':
                self.pending_hunter_shot = self.last_killed
                self.hunter_death_type = "wolf"

        # 4. 构造天亮消息
        result_text = ""
        if not self.last_killed and not self.witch_poisoned:
             result_text = (f"☀️ 天亮了！\n\n昨晚是平安夜，没有人死亡！\n\n"
                           f"存活玩家：{len(self.alive_players)}/{len(self.players)}\n\n")
        else:
            result_text = f"☀️ 天亮了！\n\n"
            if self.last_killed:
                killed_name = self._format_player_name(self.last_killed)
                result_text += f"昨晚，玩家 {killed_name} 死了！\n"
            if self.witch_poisoned:
                poisoned_name = self._format_player_name(self.witch_poisoned)
                result_text += f"同时，玩家 {poisoned_name} 死了！\n"
            result_text += f"\n存活玩家：{len(self.alive_players)}/{len(self.players)}\n\n"

        # 5. 检查胜利条件
        victory_msg, winning_faction = self.check_victory_condition()
        if victory_msg:
            result_text += f"\n🎉 {victory_msg}\n游戏结束！\n\n" + self._get_all_players_roles()
            self.phase = GamePhase.FINISHED
            await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
            await self.end_game_cleanup(winning_faction)
            return

        # 6. 游戏继续
        await self.context.send_message(self.msg_origin, MessageChain().message(result_text))

        if self.pending_hunter_shot and self.hunter_death_type == "wolf":
            hunter_id = self.pending_hunter_shot
            hunter_name = self._format_player_name(hunter_id)
            try:
                msg = (f"💀 你被狼人办掉了！\n\n🔫 你可以选择开枪带走一个人！\n\n"
                       f"请私聊使用命令：\n  /开枪 编号\n⏰ 限时{self.timeouts['hunter']}秒")
                await self.bot.send_private_msg(user_id=int(hunter_id), message=msg)
                group_msg = f"⚠️ {hunter_name} 可以选择开枪带走一个人..."
                await self.context.send_message(self.msg_origin, MessageChain().message(group_msg))
                self.timer_task = asyncio.create_task(self._hunter_shot_timeout())
                return
            except Exception as e:
                logger.error(f"[狼人杀] 通知猎人 {hunter_id} 开枪失败: {e}")

        if self.is_first_night and (self.last_killed or self.witch_poisoned):
            self.phase = GamePhase.LAST_WORDS
            await self._start_last_words()
        else:
            if self.last_killed: await self._ban_player(self.last_killed)
            if self.witch_poisoned: await self._ban_player(self.witch_poisoned)
            self.is_first_night = False
            self.last_killed = None
            self.witch_poisoned = None
            self.phase = GamePhase.DAY_SPEAKING
            await self._start_speaking_phase()
        
        self.night_result = None

    async def end_game_cleanup(self, winning_faction: str):
        try:
            ai_review = await self._generate_ai_review(winning_faction)
            if ai_review:
                await self.context.send_message(self.msg_origin, MessageChain().message(ai_review))
        except Exception as e:
            logger.error(f"[狼人杀] AI复盘发送失败: {e}")
    
    # ========== 定时器超时处理 ==========
    async def _wolf_kill_timeout(self):
        try:
            await asyncio.sleep(self.timeouts['wolf'])
            if self.phase != GamePhase.NIGHT_WOLF: return
            logger.info(f"[狼人杀] 群 {self.group_id} 狼人办掉阶段超时")
            await self.context.send_message(self.msg_origin, MessageChain().message("⏰ 狼人行动超时！自动进入下一阶段。"))
            await self._process_night_kill()
            
            self.phase = GamePhase.NIGHT_SEER
            self.seer_checked = False
            await self.context.send_message(self.msg_origin, MessageChain().message(f"🔮 狼人行动完成！\n预言家请私聊机器人验人：/验人 编号\n⏰ 剩余时间：{self.timeouts['seer']}秒"))
            
            seer_alive = any(p.role.name == "seer" and p.is_alive for p in self.players.values())
            wait_time = self.timeouts['seer'] if seer_alive else random.uniform(self.timeouts['dead_min'], self.timeouts['dead_max'])
            self.timer_task = asyncio.create_task(self._seer_check_timeout(wait_time))
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 狼人办掉定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 狼人办掉超时处理失败: {e}")

    async def _seer_check_timeout(self, wait_time: float):
        try:
            await asyncio.sleep(wait_time)
            if self.phase != GamePhase.NIGHT_SEER: return
            logger.info(f"[狼人杀] 群 {self.group_id} 预言家验人阶段超时")
            self.seer_checked = True
            
            seer_alive = any(p.role.name == "seer" and p.is_alive for p in self.players.values())
            if seer_alive:
                await self.context.send_message(self.msg_origin, MessageChain().message("⏰ 预言家验人超时！"))

            self.phase = GamePhase.NIGHT_WITCH
            self.witch_acted = False
            self.witch_saved = None
            self.witch_poisoned = None
            await self.context.send_message(self.msg_origin, MessageChain().message(f"💊 预言家验人完成！\n女巫请私聊机器人行动\n⏰ 剩余时间：{self.timeouts['witch']}秒"))
            
            witch = next((p for p in self.players.values() if p.role.name == "witch"), None)
            if witch:
                await self._notify_witch(witch.user_id)
                witch_is_killed_tonight = (self.last_killed == witch.user_id)
                wait_time = self.timeouts['witch'] if witch.is_alive or witch_is_killed_tonight else random.uniform(self.timeouts['dead_min'], self.timeouts['dead_max'])
                self.timer_task = asyncio.create_task(self._witch_timeout(wait_time))
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 预言家验人定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 预言家验人超时处理失败: {e}")

    async def _witch_timeout(self, wait_time: float):
        try:
            await asyncio.sleep(wait_time)
            if self.phase != GamePhase.NIGHT_WITCH: return
            logger.info(f"[狼人杀] 群 {self.group_id} 女巫行动阶段超时")
            self.witch_acted = True
            
            witch = next((p for p in self.players.values() if p.role.name == "witch"), None)
            if witch and witch.is_alive:
                await self.context.send_message(self.msg_origin, MessageChain().message("⏰ 女巫行动超时！视为不操作。"))
            
            await self._witch_finish()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 女巫定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 女巫超时处理失败: {e}")

    async def _last_words_timeout(self):
        try:
            await asyncio.sleep(self.timeouts['speaking'])
            if self.phase != GamePhase.LAST_WORDS: return
            logger.info(f"[狼人杀] 群 {self.group_id} 遗言阶段超时")
            
            if self.last_killed:
                await self._remove_temp_admin(self.last_killed)
                await self._ban_player(self.last_killed)
            
            await self._set_group_whole_ban(True)
            await self.context.send_message(self.msg_origin, MessageChain().message("⏰ 遗言超时！自动进入下一阶段。"))

            if self.last_words_from_vote:
                self.phase = GamePhase.NIGHT_WOLF
                self.seer_checked = False
                self.is_first_night = False
                self.last_words_from_vote = False
                self.current_round += 1
                self.game_log.extend([LOG_SEPARATOR, f"第{self.current_round}晚", LOG_SEPARATOR])
                self.timer_task = asyncio.create_task(self._wolf_kill_timeout())
                await self.context.send_message(self.msg_origin, MessageChain().message(f"🌙 夜晚降临，天黑请闭眼...\n🐺 狼人请私聊使用：/办掉 编号\n⏰ 剩余时间：{self.timeouts['wolf']}秒"))
            else:
                self.last_killed = None
                self.is_first_night = False
                self.phase = GamePhase.DAY_SPEAKING
                await self._start_speaking_phase()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 遗言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 遗言超时处理失败: {e}")

    async def _speaking_timeout(self):
        try:
            await asyncio.sleep(self.timeouts['speaking'])
            if self.phase != GamePhase.DAY_SPEAKING: return
            logger.info(f"[狼人杀] 群 {self.group_id} 发言超时")
            
            if self.current_speaker:
                await self._remove_temp_admin(self.current_speaker)
                speaker_name = self._format_player_name(self.current_speaker)
                await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ {speaker_name} 发言超时！自动进入下一位。"))

            self.current_speaker_index += 1
            await self._next_speaker()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 发言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 发言超时处理失败: {e}")

    async def _pk_speaking_timeout(self):
        try:
            await asyncio.sleep(self.timeouts['speaking'])
            if self.phase != GamePhase.DAY_PK: return
            logger.info(f"[狼人杀] 群 {self.group_id} PK发言超时")

            if self.current_speaker:
                await self._remove_temp_admin(self.current_speaker)
                speaker_name = self._format_player_name(self.current_speaker)
                await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ {speaker_name} PK发言超时！自动进入下一位。"))

            self.current_speaker_index += 1
            await self._next_pk_speaker()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} PK发言定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] PK发言超时处理失败: {e}")

    async def _day_vote_timeout(self):
        try:
            if self.timeouts['vote'] > 30:
                await asyncio.sleep(self.timeouts['vote'] - 30)
                if self.phase != GamePhase.DAY_VOTE: return
                voted_count = len(self.day_votes)
                alive_count = len(self.alive_players)
                await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ 投票倒计时：还有30秒！\n当前投票进度：{voted_count}/{alive_count}"))
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(self.timeouts['vote'])

            if self.phase != GamePhase.DAY_VOTE: return
            logger.info(f"[狼人杀] 群 {self.group_id} 白天投票阶段超时")
            await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ 投票超时！已有 {len(self.day_votes)}/{len(self.alive_players)} 人投票，自动结算。"))
            await self._process_day_vote()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 白天投票定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 白天投票超时处理失败: {e}")

    async def _hunter_shot_timeout(self):
        try:
            await asyncio.sleep(self.timeouts['hunter'])
            if not self.pending_hunter_shot: return
            logger.info(f"[狼人杀] 群 {self.group_id} 猎人开枪超时")
            
            hunter_name = self._format_player_name(self.pending_hunter_shot)
            self.pending_hunter_shot = None
            self.hunter_shot = True
            self.game_log.append(f"🔫 {hunter_name}（猎人）超时未开枪")
            await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ {hunter_name} 开枪超时！放弃开枪机会。"))

            if self.is_first_night and self.last_killed:
                self.phase = GamePhase.LAST_WORDS
                await self._start_last_words()
            else:
                if self.last_killed: await self._ban_player(self.last_killed)
                if self.witch_poisoned: await self._ban_player(self.witch_poisoned)
                self.phase = GamePhase.DAY_SPEAKING
                await self._start_speaking_phase()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 猎人开枪定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 猎人开枪超时处理失败: {e}")

    async def _hunter_shot_timeout_for_vote(self):
        try:
            await asyncio.sleep(self.timeouts['hunter'])
            if not self.pending_hunter_shot: return
            logger.info(f"[狼人杀] 群 {self.group_id} 投票后猎人开枪超时")

            hunter_id = self.pending_hunter_shot
            hunter_name = self._format_player_name(hunter_id)
            self.pending_hunter_shot = None
            self.hunter_shot = True
            self.game_log.append(f"🔫 {hunter_name}（猎人）超时未开枪")
            await self.context.send_message(self.msg_origin, MessageChain().message(f"⏰ {hunter_name} 开枪超时！放弃开枪机会。"))

            victory_msg, winning_faction = self.check_victory_condition()
            if victory_msg:
                result_text = f"🎉 {victory_msg}\n游戏结束！\n\n" + self._get_all_players_roles()
                self.phase = GamePhase.FINISHED
                await self.context.send_message(self.msg_origin, MessageChain().message(result_text))
                await self.end_game_cleanup(winning_faction)
                return

            self.phase = GamePhase.LAST_WORDS
            self.last_words_from_vote = True
            await self._start_last_words()
        except asyncio.CancelledError:
            logger.info(f"[狼人杀] 群 {self.group_id} 投票后猎人开枪定时器已取消")
        except Exception as e:
            logger.error(f"[狼人杀] 投票后猎人开枪超时处理失败: {e}")

    async def _generate_ai_review(self, winning_faction: str) -> str:
        if not self.enable_ai_review: return ""
        provider = self.context.get_provider_by_id(self.ai_review_model) if self.ai_review_model else self.context.get_using_provider()
        if not provider:
            logger.warning("[狼人杀] 无法获取LLM provider，跳过AI复盘")
            return ""

        game_data = self._format_game_data_for_ai(winning_faction)
        if self.ai_review_prompt:
            system_prompt = self.ai_review_prompt.replace("{winning_faction}", "狼人" if winning_faction == "werewolf" else "好人").replace("{game_data}", game_data)
            user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"
        else:
            system_prompt = ("你是一个资深的狼人杀游戏分析专家。请根据提供的游戏数据，生成一份专业的复盘报告。\n要求：\n1. 分析关键决策点和转折点\n2. 评价各阵营的策略和失误\n3. 指出精彩的操作和值得学习的地方\n4. 游戏日志中包含了狼人夜晚的密谋内容（标记为「💬 XXX（狼人）密谋：...」），可以适当引用原文，增加趣味性\n5. 评选出本局MVP和划水玩家\n6. 语言风格轻松幽默，但分析要专业深入\n7. 控制在1000字以内\n8. 使用emoji让内容更生动")
            user_prompt = f"请为以下狼人杀游戏生成复盘报告：\n\n{game_data}"

        response = await provider.text_chat(prompt=user_prompt, system_prompt=system_prompt)
        if response.result_chain:
            review_text = response.result_chain.get_plain_text()
            return f"\n\n🤖 AI复盘\n{LOG_SEPARATOR}\n{review_text}\n{LOG_SEPARATOR}"
        return ""

    def _format_game_data_for_ai(self, winning_faction: str) -> str:
        lines = [f"【游戏结果】\n胜利方：{'狼人' if winning_faction == 'werewolf' else '好人'}\n", "【玩家身份】"]
        role_names = {"werewolf": "狼人", "seer": "预言家", "witch": "女巫", "hunter": "猎人", "villager": "村民"}
        for p in self.players.values():
            lines.append(f"{self._format_player_name(p.user_id)} - {role_names.get(p.role.name, '未知')}")
        lines.append("\n【游戏进程】")
        lines.extend(self.game_log)
        return "\n".join(lines)
