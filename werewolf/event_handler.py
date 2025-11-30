import re
import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.message.components import At
from astrbot.core.message.message_event_result import MessageChain
from astrbot.api import logger

from .game_manager import GameManager
from .player import Player
from .game_config import PRESET_CONFIGS, GamePhase

class EventHandler:
    def __init__(self, game_manager: GameManager, context):
        self.game_manager = game_manager
        self.context = context

    async def create_room(self, event: AstrMessageEvent, player_count: int = 9):
        group_id = event.get_group_id()
        if not group_id:
            return "⚠️ 请在群聊中使用此命令！"

        if self.game_manager.get_game(group_id):
            return "❌ 当前群已存在游戏房间！请先结束现有游戏。"

        if player_count not in PRESET_CONFIGS:
            supported = ", ".join(map(str, PRESET_CONFIGS.keys()))
            return f"❌ 不支持 {player_count} 人局。\n目前支持的人数：{supported}"

        game = self.game_manager.create_game(group_id, event.get_sender_id(), event.bot, event.unified_msg_origin, player_count, self.context)
        
        cfg = game.config
        god_roles = []
        if cfg["seer"] > 0: god_roles.append(f"预言家×{cfg['seer']}")
        if cfg["witch"] > 0: god_roles.append(f"女巫×{cfg['witch']}")
        if cfg["hunter"] > 0: god_roles.append(f"猎人×{cfg['hunter']}")

        return (
            f"✅ 狼人杀房间创建成功！\n\n"
            f"📋 游戏规则：\n"
            f"• {cfg['total']}人局（{cfg['werewolf']}狼人 + {cfg['seer']+cfg['witch']+cfg['hunter']}神 + {cfg['villager']}平民）\n"
            f"• 神职：{' + '.join(god_roles)}\n"
            f"• 游戏结束后{'生成' if game.enable_ai_review else '不生成'}AI复盘\n\n"
            f"💡 使用 /加入房间 来参与游戏\n"
            f"👥 {cfg['total']}人齐全后，房主使用 /开始游戏"
        )

    async def dismiss_room(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game:
            return "❌ 当前群没有已创建的房间！"

        if event.get_sender_id() != game.creator_id:
            return "⚠️ 只有房主才能解散房间！"

        await self.game_manager.remove_game(group_id)
        return "✅ 房间已成功解散！"

    async def join_room(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game:
            return "❌ 当前群未创建房间！请使用 /创建房间"

        if game.phase != GamePhase.WAITING:
            return "❌ 游戏已开始，无法加入！"

        player_id = event.get_sender_id()
        if player_id in game.players:
            return "⚠️ 你已经在游戏中了！"

        if len(game.players) >= game.config["total"]:
            return f"❌ 房间已满（{game.config['total']}/{game.config['total']}）！"

        player_name = self._get_player_name(event)
        player = Player(player_id, player_name)
        game.add_player(player)

        return f"✅ 成功加入游戏！\n\n当前人数：{len(game.players)}/{game.config['total']}"

    async def start_game(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game:
            return "❌ 当前群没有创建的房间！"

        if event.get_sender_id() != game.creator_id:
            return "⚠️ 只有房主才能开始游戏！"

        if len(game.players) != game.config["total"]:
            return f"❌ 人数不足！当前 {len(game.players)}/{game.config['total']} 人"

        if game.phase != GamePhase.WAITING:
            return "❌ 游戏已经开始！"

        game.start()
        await game._set_group_cards_to_numbers()
        await game._set_group_whole_ban(True)
        game.timer_task = asyncio.create_task(game._wolf_kill_timeout())
        await game._send_roles_to_players()
        
        werewolves = [p.user_id for p in game.players.values() if p.role.name == "werewolf"]
        logger.info(f"[狼人杀] 群 {group_id} - 狼人: {werewolves}")

        return (
            "🌙 游戏开始！天黑请闭眼...\n\n"
            "角色已分配完毕！\n\n"
            "机器人正在私聊告知各位身份...\n"
            "如未收到私聊，请使用：/查角色\n\n"
            f"🐺 狼人请私聊使用：/办掉 编号\n"
            f"🔮 预言家请等待狼人行动完成后使用：/验人 编号\n"
            f"⏰ 剩余时间：{game.timeouts['wolf']}秒"
        )

    async def check_role(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        if not event.is_private_chat():
            return "⚠️ 请私聊机器人使用此命令！"

        game = self.game_manager.get_game_by_player(player_id)
        if not game:
            return "❌ 你没有参与任何游戏！"

        player = game.get_player(player_id)
        if not player or not player.role:
            return "❌ 游戏尚未开始，角色还未分配！"

        return f"🎭 你的角色是：\n\n{game._get_role_info_text(player)}"

    async def show_status(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game:
            return "❌ 当前群没有进行中的游戏！"

        return (
            f"📊 游戏状态\n\n"
            f"阶段：{game.phase.value}\n"
            f"存活人数：{len(game.alive_players)}/{len(game.players)}\n"
        )

    async def end_game(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game:
            return "❌ 当前群没有进行中的游戏！"

        if event.get_sender_id() != game.creator_id:
            return "⚠️ 只有房主才能结束游戏！"

        await self.game_manager.remove_game(group_id)
        return "✅ 游戏已强制结束！"

    async def werewolf_kill(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        
        player = game.get_player(player_id)
        if game.phase != GamePhase.NIGHT_WOLF: return "⚠️ 现在不是狼人行动阶段！"
        if not player or player.role.name != "werewolf": return "❌ 你不是狼人！"
        if not player.is_alive: return "❌ 你已经出局了！"

        target_str = self._get_target_user(event)
        if not target_str: return "❌ 请指定目标！\n使用：/办掉 编号"
        target_id = game._parse_target(target_str)
        if not target_id: return f"❌ 无效的目标：{target_str}"
        if not game.get_player(target_id).is_alive: return "❌ 目标玩家已经出局！"

        game.night_votes[player_id] = target_id
        game.game_log.append(f"🐺 {game._format_player_name(player_id)}（狼人）选择刀 {game._format_player_name(target_id)}")
        
        alive_werewolves = [p for p in game.players.values() if p.role.name == "werewolf" and p.is_alive]
        await event.reply(f"✅ 你选择了办掉目标！当前 {len(game.night_votes)}/{len(alive_werewolves)} 人已投票")

        if len(game.night_votes) >= len(alive_werewolves):
            await game._cancel_timer()
            await game._process_night_kill()
            
            # 检查游戏是否因狼人行动而结束
            if game.phase == GamePhase.FINISHED:
                await self.game_manager.end_game_cleanup(game.group_id, "werewolf") # 假设狼人胜利
                return "✅ 所有狼人已投票完成！游戏结束。"

            game.phase = GamePhase.NIGHT_SEER
            game.seer_checked = False
            await self.context.send_message(game.msg_origin, MessageChain().message(f"🔮 狼人行动完成！\n预言家请私聊机器人验人：/验人 编号\n⏰ 剩余时间：{game.timeouts['seer']}秒"))
            
            seer_alive = any(p.role.name == "seer" and p.is_alive for p in game.players.values())
            wait_time = game.timeouts['seer'] if seer_alive else random.uniform(game.timeouts['dead_min'], game.timeouts['dead_max'])
            game.timer_task = asyncio.create_task(game._seer_check_timeout(wait_time))
            return "✅ 所有狼人已投票完成！现在进入预言家验人阶段。"

    async def werewolf_chat(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        if not event.is_private_chat(): return "⚠️ 请私聊机器人使用此命令！"
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if not player or player.role.name != "werewolf": return "❌ 你不是狼人！"
        if not player.is_alive: return "❌ 你已经出局了！"
        if game.phase != GamePhase.NIGHT_WOLF: return "⚠️ 只能在夜晚狼人行动阶段与队友交流！"

        message_text = re.sub(r'^/?\s*(狼人杀\s*)?密谋\s*', '', event.message_str).strip()
        if not message_text: return "❌ 请输入要发送的消息！"

        teammates = [p for p in game.players.values() if p.role.name == "werewolf" and p.is_alive and p.user_id != player_id]
        if not teammates: return "❌ 没有其他存活的狼人队友！"

        sender_name = game._format_player_name(player_id)
        teammate_msg = f"🐺 队友 {sender_name} 说：\n{message_text}"
        success_count = 0
        for teammate in teammates:
            try:
                await game.bot.send_private_msg(user_id=int(teammate.user_id), message=teammate_msg)
                success_count += 1
            except Exception as e:
                logger.error(f"[狼人杀] 发送消息给狼人 {teammate.user_id} 失败: {e}")
        
        game.game_log.append(f"💬 {sender_name}（狼人）密谋：{message_text}")
        return f"✅ 消息已发送给 {success_count} 名队友！"

    async def seer_check(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if game.phase != GamePhase.NIGHT_SEER: return "⚠️ 现在不是预言家验人阶段！"
        if not player or player.role.name != "seer": return "❌ 你不是预言家！"
        if game.seer_checked: return "❌ 你今晚已经验过人了！"

        target_str = self._get_target_user(event)
        if not target_str: return "❌ 请指定验证目标！"
        target_id = game._parse_target(target_str)
        if not target_id: return f"❌ 无效的目标：{target_str}"
        if target_id == player_id: return "❌ 不能验证自己！"

        target_player = game.get_player(target_id)
        is_werewolf = target_player.role.name == "werewolf"
        game.seer_checked = True
        await game._cancel_timer()

        target_name = game._format_player_name(target_id)
        seer_name = game._format_player_name(player_id)
        result_msg = f"🔮 验人结果：\n\n玩家 {target_name} 是 {'🐺 狼人' if is_werewolf else '✅ 好人'}！"
        game.game_log.append(f"🔮 {seer_name}（预言家）验 {target_name}：{'狼人' if is_werewolf else '好人'}")
        await event.reply(result_msg)

        witch = next((p for p in game.players.values() if p.role.name == "witch"), None)
        if witch:
            game.phase = GamePhase.NIGHT_WITCH
            game.witch_acted = False
            game.witch_saved = None
            game.witch_poisoned = None
            await self.context.send_message(game.msg_origin, MessageChain().message(f"💊 预言家验人完成！\n女巫请私聊机器人行动\n⏰ 剩余时间：{game.timeouts['witch']}秒"))
            await game._notify_witch(witch.user_id)
            
            witch_is_killed_tonight = (game.last_killed == witch.user_id)
            wait_time = game.timeouts['witch'] if witch.is_alive or witch_is_killed_tonight else random.uniform(game.timeouts['dead_min'], game.timeouts['dead_max'])
            game.timer_task = asyncio.create_task(game._witch_timeout(wait_time))
            return "✅ 预言家验人完成！现在进入女巫行动阶段。"
        else:
            logger.error(f"[狼人杀] 游戏配置错误：找不到女巫角色")
            return "❌ 游戏配置错误！"

    async def witch_save(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if game.phase != GamePhase.NIGHT_WITCH: return "⚠️ 现在不是女巫行动阶段！"
        if not player or player.role.name != "witch": return "❌ 你不是女巫！"
        if game.witch_acted: return "❌ 你今晚已经行动过了！"
        if game.witch_antidote_used: return "❌ 解药已经用过了！"
        if not game.last_killed: return "❌ 今晚没有人被杀，无法使用解药！"

        game.witch_saved = game.last_killed
        game.witch_antidote_used = True
        game.witch_acted = True
        await game._cancel_timer()

        saved_name = game._format_player_name(game.last_killed)
        witch_name = game._format_player_name(player_id)
        game.game_log.append(f"💊 {witch_name}（女巫）使用解药救了 {saved_name}")
        await event.reply(f"✅ 你使用解药救了 {saved_name}！")
        await game._witch_finish()

    async def witch_poison(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if game.phase != GamePhase.NIGHT_WITCH: return "⚠️ 现在不是女巫行动阶段！"
        if not player or player.role.name != "witch": return "❌ 你不是女巫！"
        if game.witch_acted: return "❌ 你今晚已经行动过了！"
        if game.witch_poison_used: return "❌ 毒药已经用过了！"

        target_str = self._get_target_user(event)
        if not target_str: return "❌ 请指定毒人目标！"
        target_id = game._parse_target(target_str)
        if not target_id: return f"❌ 无效的目标：{target_str}"
        if not game.get_player(target_id).is_alive: return "❌ 目标玩家已经出局！"
        if target_id == player_id: return "❌ 不能毒自己！"

        game.witch_poisoned = target_id
        game.witch_poison_used = True
        game.witch_acted = True
        await game._cancel_timer()

        poisoned_name = game._format_player_name(target_id)
        witch_name = game._format_player_name(player_id)
        game.game_log.append(f"💊 {witch_name}（女巫）使用毒药毒了 {poisoned_name}")
        await event.reply(f"✅ 你使用毒药毒了 {poisoned_name}！")
        await game._witch_finish()

    async def witch_pass(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if game.phase != GamePhase.NIGHT_WITCH: return "⚠️ 现在不是女巫行动阶段！"
        if not player or player.role.name != "witch": return "❌ 你不是女巫！"
        if game.witch_acted: return "❌ 你今晚已经行动过了！"

        game.witch_acted = True
        await game._cancel_timer()
        game.game_log.append(f"💊 {game._format_player_name(player_id)}（女巫）选择不操作")
        await event.reply("✅ 你选择不操作！")
        await game._witch_finish()

    async def finish_last_words(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game: return "❌ 当前群没有进行中的游戏！"
        player_id = event.get_sender_id()
        if game.phase != GamePhase.LAST_WORDS: return "⚠️ 现在不是遗言阶段！"
        if game.last_killed != player_id: return "⚠️ 只有被杀的玩家才能使用此命令！"

        await game._cancel_timer()
        player_name = game._format_player_name(player_id)
        if game.current_speech:
            full_speech = " ".join(game.current_speech)
            game.game_log.append(f"💀遗言：{player_name} - {full_speech[:200]}")
        else:
            game.game_log.append(f"💀遗言：{player_name} - [未捕获到文字内容]")
        
        game.current_speech = []
        await game._remove_temp_admin(player_id)
        await game._ban_player(player_id)
        await game._set_group_whole_ban(True)
        await event.reply("✅ 遗言完毕！")

        if game.last_words_from_vote:
            game.phase = GamePhase.NIGHT_WOLF
            game.seer_checked = False
            game.is_first_night = False
            game.last_words_from_vote = False
            game.current_round += 1
            game.game_log.extend(["="*30, f"第{game.current_round}晚", "="*30])
            game.timer_task = asyncio.create_task(game._wolf_kill_timeout())
            await self.context.send_message(game.msg_origin, MessageChain().message(f"🌙 夜晚降临，天黑请闭眼...\n🐺 狼人请私聊使用：/办掉 编号\n⏰ 剩余时间：{game.timeouts['wolf']}秒"))
        else:
            game.last_killed = None
            game.is_first_night = False
            game.phase = GamePhase.DAY_SPEAKING
            await game._start_speaking_phase()

    async def finish_speaking(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game: return "❌ 当前群没有进行中的游戏！"
        player_id = event.get_sender_id()
        if game.phase not in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK]: return "⚠️ 现在不是发言阶段！"
        if game.current_speaker != player_id: return "⚠️ 现在不是你的发言时间！"

        await game._cancel_timer()
        player_name = game._format_player_name(player_id)
        if game.current_speech:
            full_speech = " ".join(game.current_speech)
            phase_tag = "💬PK发言" if game.phase == GamePhase.DAY_PK else "💬发言"
            game.game_log.append(f"{phase_tag}：{player_name} - {full_speech[:200]}")
        else:
            phase_tag = "💬PK发言" if game.phase == GamePhase.DAY_PK else "💬发言"
            game.game_log.append(f"{phase_tag}：{player_name} - [未捕获到文字内容]")
        
        game.current_speech = []
        await game._remove_temp_admin(player_id)
        await event.reply("✅ 发言完毕！")

        if game.phase == GamePhase.DAY_PK:
            game.current_speaker_index += 1
            await game._next_pk_speaker()
        else:
            game.current_speaker_index += 1
            await game._next_speaker()

    async def start_vote(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game: return "❌ 当前群没有进行中的游戏！"
        if event.get_sender_id() != game.creator_id: return "⚠️ 只有房主才能跳过发言环节！"
        if game.phase not in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK]: return "⚠️ 现在不是发言阶段！"

        await game._cancel_timer()
        if game.current_speaker:
            await game._remove_temp_admin(game.current_speaker)
        
        await event.reply("✅ 房主跳过发言环节，直接进入投票！")
        if game.phase == GamePhase.DAY_PK:
            await game._start_pk_vote()
        else:
            await game._auto_start_vote()

    async def day_vote(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game: return "❌ 当前群没有进行中的游戏！"
        player_id = event.get_sender_id()
        if game.phase != GamePhase.DAY_VOTE: return "⚠️ 现在不是投票阶段！"
        player = game.get_player(player_id)
        if not player: return "❌ 你不在游戏中！"
        if not player.is_alive: return "❌ 你已经出局了！"

        target_str = self._get_target_user(event)
        if not target_str: return "❌ 请指定投票目标！\n使用：/投票 编号 (输入 0 弃票)"

        if target_str == "0":
            target_id = "ABSTAIN"
        else:
            target_id = game._parse_target(target_str)

        if target_id != "ABSTAIN":
            if not target_id: return f"❌ 无效的目标：{target_str}"
            if not game.get_player(target_id).is_alive: return "❌ 目标玩家已经出局！"
            if game.is_pk_vote and target_id not in game.pk_players:
                pk_names = [game._format_player_name(pid) for pid in game.pk_players]
                return f"❌ PK投票只能投给平票玩家！\n可投票对象：\n" + "\n".join([f"  • {name}" for name in pk_names])

        game.day_votes[player_id] = target_id
        voter_name = game._format_player_name(player_id)
        if target_id == "ABSTAIN":
            log_msg = f"🗳️ {voter_name} 弃票"
            await event.reply(f"✅ 你选择了弃票！当前已投票 {len(game.day_votes)}/{len(game.alive_players)} 人")
        else:
            target_name = game._format_player_name(target_id)
            log_msg = f"🗳️ {voter_name} 投票给 {target_name}"
            await event.reply(f"✅ 投票成功！当前已投票 {len(game.day_votes)}/{len(game.alive_players)} 人")
        
        if game.is_pk_vote: log_msg = "PK投票：" + log_msg
        game.game_log.append(log_msg)

        if len(game.day_votes) >= len(game.alive_players):
            await game._cancel_timer()
            await game._process_day_vote()

    async def hunter_shoot(self, event: AstrMessageEvent):
        player_id = event.get_sender_id()
        if not event.is_private_chat(): return "⚠️ 请私聊机器人使用此命令！"
        game = self.game_manager.get_game_by_player(player_id)
        if not game: return "❌ 你没有参与任何游戏！"
        player = game.get_player(player_id)
        if not player or player.role.name != "hunter": return "❌ 你不是猎人！"
        if game.pending_hunter_shot != player_id: return "❌ 当前不能开枪！"
        if game.hunter_death_type == "poison": return "❌ 你被女巫毒死，不能开枪！"

        target_str = self._get_target_user(event)
        if not target_str: return "❌ 请指定目标！"
        target_id = game._parse_target(target_str)
        if not target_id: return f"❌ 无效的目标：{target_str}"
        if not game.get_player(target_id).is_alive: return f"❌ {game._format_player_name(target_id)} 已经出局！"
        if target_id == player_id: return "❌ 不能开枪带走自己！"

        game.get_player(target_id).is_alive = False
        game.hunter_shot = True
        game.pending_hunter_shot = None
        target_name = game._format_player_name(target_id)
        hunter_name = game._format_player_name(player_id)
        game.game_log.append(f"🔫 {hunter_name}（猎人）开枪带走 {target_name}")
        await event.reply(f"💥 你开枪带走了 {target_name}！")
        await game._ban_player(target_id)
        await self.context.send_message(game.msg_origin, MessageChain().message(f"💥 猎人开枪带走了 {target_name}！\n剩余存活玩家：{len(game.alive_players)} 人"))
        await game._cancel_timer()

        victory_msg, winning_faction = game.check_victory_condition()
        if victory_msg:
            result_text = f"🎉 {victory_msg}\n游戏结束！\n\n" + game._get_all_players_roles()
            game.phase = GamePhase.FINISHED
            await self.context.send_message(game.msg_origin, MessageChain().message(result_text))
            await self.game_manager.end_game_cleanup(game.group_id, winning_faction)
            return

        if game.hunter_death_type == "vote":
            game.phase = GamePhase.LAST_WORDS
            game.last_killed = player_id
            game.last_words_from_vote = True
            await game._start_last_words()
        elif game.hunter_death_type == "wolf":
            if game.is_first_night and (game.last_killed or game.witch_poisoned):
                game.phase = GamePhase.LAST_WORDS
                await game._start_last_words()
            else:
                if game.last_killed: await game._ban_player(game.last_killed)
                game.phase = GamePhase.DAY_SPEAKING
                await game._start_speaking_phase()

    def show_help(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        
        if game:
            cfg = game.config
            god_num = cfg['seer'] + cfg['witch'] + cfg['hunter']
            current_room_info = (f"\n📊 当前房间配置：\n• 总人数：{cfg['total']}人\n"
                                 f"• 配置：{cfg['werewolf']}狼 + {god_num}神 + {cfg['villager']}民\n"
                                 f"  (预言家{cfg['seer']}, 女巫{cfg['witch']}, 猎人{cfg['hunter']})")
            max_number = cfg['total']
        else:
            max_number = "N" 
            current_room_info = "\n💡 提示：使用 /创建房间 [人数] 可查看不同人数的配置详情。"

        supported_players = "/".join(map(str, PRESET_CONFIGS.keys()))
        help_text = ("📖 狼人杀游戏 - 命令列表\n\n"
                     "基础命令：\n"
                     f"  /创建房间 [人数] - (支持: {supported_players}人)\n"
                     "  /解散房间 - （房主）\n"
                     "  /加入房间\n"
                     "  /开始游戏 - （房主）\n"
                     "  /查角色 - （私聊）\n"
                     "  /游戏状态\n"
                     "  /结束游戏 - （房主）\n\n"
                     f"游戏命令（编号 1-{max_number}）：\n"
                     "  /办掉 编号\n"
                     "  /密谋 消息\n"
                     "  /验人 编号\n"
                     "  /毒人 编号\n"
                     "  /救人\n"
                     "  /不操作\n"
                     "  /开枪 编号\n"
                     "  /发言完毕\n"
                     "  /遗言完毕\n"
                     "  /投票 编号\n"
                     "  /开始投票 - （房主）\n\n"
                     "游戏规则：\n"
                     "• 胜利条件：🐺 狼人胜利：好人数量 ≤ 狼人 或 神职全灭 | ✅ 好人胜利：狼人全部出局\n"
                     "• 遗言规则：第一晚被狼杀、投票放逐有遗言，被毒无遗言\n"
                     "• 猎人技能：被狼杀或投票放逐可开枪，被毒不能开枪\n"
                     f"• 游戏复盘：{'开启' if not game or game.enable_ai_review else '关闭'}\n"
                     f"{current_room_info}")
        return help_text

    async def capture_speech(self, event: AstrMessageEvent):
        group_id = event.get_group_id()
        game = self.game_manager.get_game(group_id)
        if not game: return
        player_id = event.get_sender_id()

        if game.phase == GamePhase.LAST_WORDS:
            if game.last_killed != player_id: return
        elif game.phase in [GamePhase.DAY_SPEAKING, GamePhase.DAY_PK]:
            if game.current_speaker != player_id: return
        else:
            return

        message_text = event.get_message_outline()
        if message_text.startswith("/"): return
        if message_text.strip():
            game.current_speech.append(message_text)
            logger.debug(f"[狼人杀] 捕获发言: {game._format_player_name(player_id)}: {message_text[:50]}")

    def _get_player_name(self, event: AstrMessageEvent) -> str:
        try:
            sender = (hasattr(event, 'unified_msg_origin') and event.unified_msg_origin and hasattr(event.unified_msg_origin, 'sender') and event.unified_msg_origin.sender) or \
                     (hasattr(event, 'sender') and event.sender) or \
                     (hasattr(event, 'message_obj') and hasattr(event.message_obj, 'sender') and event.message_obj.sender)
            if sender:
                if isinstance(sender, dict):
                    return sender.get('card') or sender.get('nickname') or sender.get('name') or f"玩家{event.get_sender_id()[-4:]}"
                else:
                    return getattr(sender, 'card', None) or getattr(sender, 'nickname', None) or f"玩家{event.get_sender_id()[-4:]}"
            return f"玩家{event.get_sender_id()[-4:]}"
        except Exception:
            return f"玩家{event.get_sender_id()[-4:]}"

    def _get_target_user(self, event: AstrMessageEvent) -> str:
        for seg in event.get_messages():
            if isinstance(seg, At):
                return str(seg.qq)
        for seg in event.get_messages():
            if hasattr(seg, 'text'):
                match = re.search(r'\b(\d+)\b', seg.text)
                if match:
                    return match.group(1)
        return ""
