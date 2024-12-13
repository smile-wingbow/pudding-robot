# -*- coding: utf-8 -*-
import json
import random
import time

from metagpt.roles import Role
from metagpt.config2 import Config
from metagpt.schema import Message
from metagpt.roles.role import RoleReactMode
from metagpt.actions.add_requirement import UserRequirement
from metagpt.actions import Action
from metagpt.logs import logger

from pathlib import Path
from typing import Any, Callable, ClassVar

class Gossip(Action):
    name: str = "Gossip"

    run: ClassVar[callable]

    GOSSIP_PROMPT_TEMPLATE: str = """     
    {profile}， 你在和人聊天，聊天时会顺着对方的思路往下讲，以下是聊天记录：
    <<<
    {context}
    >>>，
    输出不超过300个字符。
    """

    def __init__(self, tts_callback_sync, listen_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback_sync = tts_callback_sync
        self.listen_callback = listen_callback

    async def run(self, context, nickname, playmate, llm_config):
        pardon = [f"你还在吗？", f"我没听清呢。", f"你能再说一遍吗?"]
        exit_gossip = [f"我们下次再聊吧", f"我们今天就聊到这里吧", f"我们休息一下吧"]

        dialogue_history = []
        while True:
            gossip_prompt = self.GOSSIP_PROMPT_TEMPLATE.format(profile=llm_config["prompt"], context=context)

            logger.info(f"gossip 提示词：{gossip_prompt}")

            rsp_gossip = await self._aask(gossip_prompt)

            logger.info(rsp_gossip)

            self.tts_callback_sync(rsp_gossip, volume=20, speed_ratio=1, voice_type=llm_config["voiceType"], cache=False)

            answer = self.listen_callback(silent_count_threshold=40, recording_timeout=120, silent=False)

            if answer.strip():
                # 将新的一轮对话添加到历史记录列表
                dialogue_history.append(f'《《你：{rsp_gossip}》》,《《对方：{answer}》》')
            else:
                self.tts_callback_sync(random.choice(pardon), volume=20, speed_ratio=1, voice_type=llm_config["voiceType"], cache=False)
                pardon_reply = self.listen_callback(silent_count_threshold=20, recording_timeout=60)
                if pardon_reply.strip():
                    context = context + f'《《你：{rsp_gossip}》》,《《对方：{pardon_reply}》》,'
                else:
                    self.tts_callback_sync(random.choice(exit_gossip), volume=20, speed_ratio=1, voice_type=llm_config["voiceType"], cache=False)
                    return ""

                # 保留最近的三轮对话
            if len(dialogue_history) > 2:
                dialogue_history.pop(0)

                # 将对话历史重新拼接为字符串形式的context
            context = ','.join(dialogue_history)


class StoryBot(Role):
    name: str = "StoryBot"
    profile: str = "StoryBot"

    _act: ClassVar[callable]

    def __init__(self, nickname, playmate, tts_callback_sync, listen_callback, **kwargs):
        super().__init__(**kwargs)

        self.nickname = nickname
        self.playmate = playmate
        self.tts_callback_sync = tts_callback_sync
        self.listen_callback = listen_callback


        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))
        doubao_lite_4k_llm = Config.from_yaml_file(Path("config/doubao_lite_4k.yaml"))
        doubao_lite_32k_llm = Config.from_yaml_file(Path("config/doubao_lite_32k.yaml"))
        doubao_pro_32k = Config.from_yaml_file(Path("config/doubao_pro_32k.yaml"))
        glm3_130B_llm = Config.from_yaml_file(Path("config/glm3_130B.yaml"))
        moonshot_8k_llm = Config.from_yaml_file(Path("config/moonshot_8k.yaml"))

        self._watch([UserRequirement])
        self.set_actions([Gossip(self.tts_callback_sync, self.listen_callback, config=doubao_lite_4k_llm)])

    async def _act(self) -> Message:
        todo = self.rc.todo
        # context = self.get_memories(k=0)
        context = self.get_memories()
        length = len(context)
        content = json.loads(context[length - 1].content)
        user_input = content["user_input"]
        llm_config = content["llm_config"]

        code_text = await todo.run(user_input, self.nickname, self.playmate, llm_config)
        msg = Message(content=code_text, role=self.name, cause_by=type(todo))
        return msg