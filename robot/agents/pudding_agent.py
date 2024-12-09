# -*- coding: utf-8 -*-
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
from datetime import datetime
import ruamel.yaml
from ruamel.yaml.compat import StringIO
import json
import re
import tiktoken
import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading


def truncated_string(
        string: str,
        model: str,
        max_tokens: int,
        print_warning: bool = True,
) -> str:
    """Truncate a string to a maximum number of tokens."""
    encoding = tiktoken.encoding_for_model(model)
    encoded_string = encoding.encode(string)
    truncated_string = encoding.decode(encoded_string[:max_tokens])
    if print_warning and len(encoded_string) > max_tokens:
        print(f"Warning: Truncated string from {len(encoded_string)} tokens to {max_tokens} tokens.")
    return truncated_string

def parse_jason_code(rsp):
    pattern = r"```json(.*)```"
    match = re.search(pattern, rsp, re.DOTALL)
    code_text = match.group(1) if match else rsp
    return code_text

def parse_yaml_code(rsp):
    pattern = r"```yaml(.*?)```"
    match = re.search(pattern, rsp, re.DOTALL)
    code_text = match.group(1) if match else rsp
    return code_text

def is_json(json_str):
    try:
        json_object = json.loads(json_str)
    except ValueError as e:
        return False
    return True

def is_yaml(yaml_str):
    yaml = ruamel.yaml.YAML()
    try:
        yaml_object = yaml.load(yaml_str)
        if not isinstance(yaml_object, dict):  # 检查是否为字典
            return False
    except ValueError as e:
        return False
    return True

def get_all_books():
    conn_book = sqlite3.connect('./story_db/picture_books.db')
    cursor_book = conn_book.cursor()
    # 执行查询
    cursor_book.execute("SELECT id, cn_title FROM t_picture_book")

    # 获取所有记录
    rows = cursor_book.fetchall()

    # 关闭连接
    conn_book.commit()
    cursor_book.close()
    conn_book.close()

    # 提取 cn_title 列
    titles_with_ids = [(row[0], row[1]) for row in rows]
    return titles_with_ids

def get_book_description_by_id(id):
    conn_book = sqlite3.connect('./story_db/picture_books.db')
    cursor_book = conn_book.cursor()

    # 执行查询
    cursor_book.execute('''
    SELECT
        t_picture_book.cn_title,
        t_picture_book.en_title,
        t_picture_book.cn_subtitle,
        t_picture_book.en_subtitle,
        t_picture_book.picture_content  
    FROM
        t_picture_book
    WHERE
        t_picture_book.id = ?
    ''', (id,))

    # 获取所有记录
    row = cursor_book.fetchone()

    # 关闭连接
    cursor_book.close()
    conn_book.close()

    return row

def get_book_content_by_book_id(book_id):
    conn_book = sqlite3.connect('./story_db/picture_books.db')
    cursor_book = conn_book.cursor()

    # 执行查询
    cursor_book.execute('''
    SELECT
        t_picture_book_content.id,
        t_picture_book_content.sequence,
        t_picture_book_content.picture_content,
        t_picture_book_content.sequence
    FROM
        t_picture_book_content
    WHERE
        t_picture_book_content.book_id = ?
    ORDER BY
        t_picture_book_content.sequence;
    ''', (book_id,))

    # 获取所有记录
    rows = cursor_book.fetchall()

    # 关闭连接
    cursor_book.close()
    conn_book.close()

    return rows

def get_book_text_by_content_id(content_id):
    conn_book = sqlite3.connect('./story_db/picture_books.db')
    cursor_book = conn_book.cursor()

    # 执行查询
    cursor_book.execute('''
    SELECT
        t_picture_book_text.id,
        t_picture_book_text.language,
        t_picture_book_text.text,
        t_picture_book_text.type,
        t_picture_book_text.character,
        t_picture_book_text.character_category,
        t_picture_book_text.sequence
    FROM
        t_picture_book_text
    WHERE
        t_picture_book_text.content_id = ?
    ORDER BY
        t_picture_book_text.sequence;
    ''', (content_id,))

    # 获取所有记录
    rows = cursor_book.fetchall()

    # 关闭连接
    cursor_book.close()
    conn_book.close()

    return rows

class TellStory(Action):
    name: str = "PlayMedia"

    COVER_QUESTION_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个知识渊博的年轻妈妈，你擅长给你的{years}岁的宝贝讲绘本故事时，向小宝贝提一些启发性的问题
    - 小宝贝的昵称是{nickname}

    ## Constraints:
    - 以一个妈妈的身份，用充满爱心的口吻提出问题，
    - 你绝不能自称"妈妈"
    - 只提一个问题，不要超过1个    

    ## Workflow:
    你在给你的小宝贝读一本绘本，绘本的名字是"{cn_title}"，现在开始读绘本，绘本的封面是《{picture_discription}》，
    现在你在给宝贝讲绘本之前，先用妈妈的口吻和满怀爱心的语气，引导小宝贝仔细观察这个绘本的封面，在引导小宝贝后提出一个适合{years}岁小宝贝的《{questions}》，启发小宝贝的思维，

    ## OutputFormat:
    结果以yaml结构输出，yaml格式如下：
    ```yaml 
    question:
    ```
    """

    QUESTION_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个知识渊博的年轻妈妈，你擅长给你的{years}岁的宝贝讲绘本故事时，向小宝贝提一些启发性的问题
    - 小宝贝的昵称是{nickname}

    ## Constraints:
    - 以一个妈妈的身份，用充满爱心的口吻提出问题，
    - 你绝不能自称"妈妈"

    ## Workflow:
    你在给你的小宝贝读一本绘本，绘本的名字是"{cn_title}"，现在在读的这一页，画面是《{picture_discription}》，文字内容是《{content_texts}》，
    现在你要给你的宝贝提一个适合{years}岁小宝贝的《{questions}》，启发小宝贝的思维，问题不超过20个字
    
    ## OutputFormat:
    结果以yaml结构输出，yaml格式如下：
    ```yaml 
    question:
    ```
    """

    REPLY_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个知识渊博的年轻妈妈，你擅长给你的{years}岁的宝贝讲绘本故事时，向小宝贝提一些启发性的问题
    - 小宝贝的昵称是{nickname}

    ## Constraints:
    - 以一个妈妈的身份，用充满爱心的口吻对小宝贝的话进行回复，
    - 你绝不能自称"妈妈"

    ## Workflow:
    你在给你的小宝贝读一本绘本，绘本的名字是《{cn_title}》，现在在读的这一页，画面是'{picture_discription}'，文字内容是'{content_texts}'，
    现在你向你的宝贝提了一个问题：'{question}'，小宝贝的回答是'{answer}'，你现在要回答你的宝贝，输出{{reply}}。
    
    ## OutputFormat:
    结果以json结构输出，json格式如下：
    ```json
    {{"reply":""}}
    ```
    """

    run: ClassVar[callable]

    def __init__(self, tts_callback, tts_callback_sync, tts_callback_with_priority, listen_callback, setStoryMode, set_book_id, set_book_content_id, set_book_content_sequence, set_book_content_text_id, set_book_content_text_sequence, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback
        self.tts_callback_sync = tts_callback_sync
        self.tts_callback_with_priority = tts_callback_with_priority
        self.listen_callback = listen_callback
        self.setStoryMode = setStoryMode
        self.set_book_id = set_book_id
        self.set_book_content_id = set_book_content_id
        self.set_book_content_sequence = set_book_content_sequence
        self.set_book_content_text_id = set_book_content_text_id
        self.set_book_content_text_sequence = set_book_content_text_sequence

        self.executor = ThreadPoolExecutor(max_workers=2)

        self.lock = asyncio.Lock()  # 用于控制朗读任务的锁

    async def generate_random_question(self, book_cn_title, book_content_picture_discription, book_content_texts):
        logger.info("generate_random_question---------------------")
        questions = [
            "观察性问题：让孩子描述页面上的物体或人物",
            "情感问题：询问孩子对角色的感受",
            "预测性问题：引导孩子思考接下来的情节",
            "想象性问题：鼓励孩子发挥想象力",
            "连接性问题：将故事与孩子的生活联系起来",
            "比较性问题：让孩子对比角色或情节",
            "具体细节问题：询问关于页面具体细节的问题",
            "因果关系问题：引导孩子思考事件之间的关系",
            "分类问题：让孩子对页面中的物体进行分类",
            "总结性问题：在翻页之前，问孩子对这一页的总结",
            "解决问题问题：引导孩子思考解决方案",
            "情境重演问题：询问孩子如何在类似情况下反应",
            "时间问题：让孩子思考故事的时间线",
            "描述变化问题：引导孩子描述角色或环境的变化",
            "多样性问题：询问孩子关于角色或环境的多样性",
            "情感表达问题：引导孩子表达情感",
            "动机探讨问题：询问孩子关于角色行为背后的原因",
            "空间感知问题：询问孩子关于环境的问题",
            "模仿行为问题：问孩子如果她是故事中的角色，会怎么做",
            "声音想象问题：让孩子想象声音",
            "物体用途问题：询问孩子关于页面上物体的用途",
            "情节改变问题：让孩子想象如果改变一个小细节，故事会怎样发展",
            "回忆问题：让孩子回忆自己的经历"
        ]
        if random.random() < 0.5:
            question_prompt = self.QUESTION_PROMPT_TEMPLATE.format(years="4", cn_title=book_cn_title, picture_discription=book_content_picture_discription, content_texts=book_content_texts, questions=random.choice(questions), nickname="小宝贝")
            logger.info(f"主动提问提示词：{question_prompt}")
            question_rsp = await self._aask(question_prompt)
            print(question_rsp)
            await asyncio.sleep(0.5)  # 等待 0.5 秒
            return question_rsp, book_cn_title, book_content_picture_discription, book_content_texts
        else:
            return None

    def _run_async_in_thread(self, async_func, *args, **kwargs):
        """
        在新线程中运行异步函数，并支持阻塞等待
        :param async_func: 异步函数
        :param args: 异步函数的参数
        :param kwargs: 异步函数的关键字参数
        """

        # 创建一个 Event 对象用于阻塞
        event = threading.Event()

        def run():
            loop = asyncio.new_event_loop()  # 创建新的事件循环
            asyncio.set_event_loop(loop)

            # 运行异步函数，并在完成后设置 event
            loop.run_until_complete(async_func(*args, **kwargs))
            event.set()  # 当异步函数执行完，解除阻塞
            loop.close()

        # 启动新线程
        threading.Thread(target=run).start()

        # 阻塞直到异步函数完成
        event.wait()

    async def reply_for_question(self, question, cn_title, picture_discription, content_texts):
        logger.info(f"picture_discription:{picture_discription}-------------------------------------")
        answer = self.listen_callback(silent_count_threshold=20, recording_timeout=60)
        reply_prompt = self.REPLY_PROMPT_TEMPLATE.format(years="4", question=question, cn_title=cn_title,
                                                         picture_discription=picture_discription,
                                                         content_texts=content_texts,
                                                         answer=answer, nickname="小宝贝")
        logger.info(f"答复提示词：{reply_prompt}")
        reply_rsp = await self._aask(reply_prompt)
        print(reply_rsp)
        json_str = parse_jason_code(reply_rsp)
        if json_str and is_json(json_str):  # 确保匹配到的内容存在
            data = json.loads(json_str)
            self.tts_callback_with_priority(data["reply"], volume=20, cache_play_silence_duration=2.5, speed_ratio=0.8, emotion="happy", cache=True)

    async def run(self, context, book_id, book_content_id, book_content_sequence, book_content_text_id, book_content_text_sequence):
        yaml = ruamel.yaml.YAML()
        yaml.preserve_quotes = True  # 尝试保留原始引号
        yaml.indent(mapping=2, sequence=4, offset=2)

        open_book = ["现在我们翻开第一页", "现在我们翻开书", "小宝贝，来把书翻开", "让我们来打开第一页"]
        next_page = ["让我们翻开下一页", "现在看看下一页讲了什么", "接下来我们来看下一页", "好了，来看下一页", "看看下一页讲什么"]
        finish = ["现在，故事讲完了", "现在，就讲到这里吧", "好了，这个故事讲完了", "这本书就讲到这里吧", "就讲到这儿吧"]

        book_description = get_book_description_by_id(book_id)
        if book_description:
            # 进入故事模式
            self.setStoryMode(True)
            self.set_book_id(book_id)
            print(book_description)
            # 判断是否是打断后继续
            if book_content_id is None and book_content_text_id is None:
                # 读书名
                self.tts_callback(book_description[0], volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

            # 随机读英文书名
            if book_description[1]:
                # 判断是否是打断后继续
                if book_content_id is None and book_content_text_id is None:
                    self.tts_callback(book_description[1], volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

            # 随机读中文副标题和英文副标题
            if book_description[2]:
                # 中文子标题50%概率随机阅读
                if random.random() < 0.5:
                    # 判断是否是打断后继续
                    if book_content_id is None and book_content_text_id is None:
                        self.tts_callback(book_description[2], volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

                    if book_description[3]:
                        # 英文子标题30%随机阅读
                        if random.random() < 0.3:
                            # 判断是否是打断后继续
                            if book_content_id is None and book_content_text_id is None:
                                self.tts_callback(book_description[3], volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

            # 判断是否是打断后继续
            if book_content_id is None and book_content_text_id is None:
                # 读绘本封面
                self.tts_callback(book_description[4], volume=20, cache_play_silence_duration=2.5, speed_ratio=0.8, emotion="happy", cache=True)

            # 判断是否是打断后继续
            if book_content_id is None and book_content_text_id is None:
                self.tts_callback(random.choice(open_book), volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

            first_page = True

            book_contents = get_book_content_by_book_id(book_id)
            if book_contents:
                for content in book_contents:
                    self.set_book_content_id(content[0])
                    self.set_book_content_sequence(content[3])
                    # 获取绘本的文本内容
                    book_content_texts = get_book_text_by_content_id(content[0])

                    logger.info(f"随机问题生成素材：{book_description[0]}，{content[2]}-----------------")

                    # 生成随机问题
                    loop = asyncio.get_event_loop()
                    background_future = loop.run_in_executor(self.executor, asyncio.run,
                                                             self.generate_random_question(book_description[0],
                                                                                           content[2],
                                                                                           book_content_texts))

                    if not first_page:
                        # 判断是否是打断后继续
                        if book_content_id is None or (book_content_sequence is not None and content[3] > book_content_sequence):
                            self.tts_callback(random.choice(next_page), volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

                    first_page = False
                    print(content)

                    if book_content_texts:
                        for content_text in book_content_texts:
                            self.set_book_content_text_id(content_text[0])
                            self.set_book_content_text_sequence(content_text[6])
                            print(content_text)
                            # 判断是否是打断后继续
                            if book_content_text_id is None or (book_content_text_sequence is not None and content_text[6] > book_content_text_sequence):
                                # 读绘本页面文字
                                self.tts_callback(content_text[2], volume=20, cache_play_silence_duration=2.5, speed_ratio=0.8, character_category=content_text[5], cache=True)

                    # 判断是否是打断后继续
                    if book_content_id is None or (book_content_sequence is not None and content[3] > book_content_sequence):
                        # 读绘本页面描述
                        self.tts_callback(content[2], volume=20, cache_play_silence_duration=2.5, speed_ratio=0.8, emotion="happy", cache=True)

                    # 判断是否是打断后继续
                    if book_content_id is None or (book_content_sequence is not None and content[3] >= book_content_sequence):
                        # 检查生成问题的结果
                        result = await background_future
                        if result is not None:
                            # 如果后台任务已经完成，获取结果
                            question_rsp, book_cn_title, book_content_picture_discription, book_content_texts = result
                            logger.info(f"return question_rsp, book_cn_title, book_content_picture_discription, book_content_texts:{question_rsp}, {book_cn_title}, {book_content_picture_discription}, {book_content_texts}")
                            if question_rsp is not None:
                                yaml_str = parse_yaml_code(question_rsp)
                                if yaml_str and is_yaml(yaml_str):  # 确保匹配到的内容存在
                                    data = yaml.load(StringIO(yaml_str))
                                    self.tts_callback(data["question"], volume=20, cache_play_silence_duration=2.5, speed_ratio=0.8, emotion="happy", cache=True,
                                                      onCompleted=lambda q=data["question"], ct=book_cn_title, pd=book_content_picture_discription,
                                                                         cts=book_content_texts: self._run_async_in_thread(
                                                          self.reply_for_question, q, ct, pd, cts))
                        else:
                            # 如果后台任务未完成，可以选择等待或忽略
                            print("后台任务还未完成。")

            self.tts_callback(random.choice(finish), volume=20, cache_play_silence_duration=1.5, speed_ratio=0.8, emotion="happy", cache=True)

        self.tts_callback("", volume=20, cache_play_silence_duration=1.5, clearBook=True)

        # # 清空当前播放绘本记录
        # self.setStoryMode(False)
        # self.set_book_id(None)
        # self.set_book_content_id(None)
        # self.set_book_content_sequence(None)
        # self.set_book_content_text_id(None)
        # self.set_book_content_text_sequence(None)

        return "读完了"


class PlayMedia(Action):
    name: str = "PlayMedia"

    run: ClassVar[callable]

    def __init__(self, **data: Any):
        super().__init__(**data)

    async def run(self, context, audio):
        logger.info(f"play media:{context}")

        rsp_result = context

        return rsp_result


class Gossip(Action):
    name: str = "Gossip"

    run: ClassVar[callable]

    # GOSSIP_PROMPT_TEMPLATE: str = """
    # ## Profile:
    # - Language:中文
    # - 你现在扮演一个5岁的小男孩，名字叫"{playmate}"，个性顽皮、活泼，爱说话，喜欢和其他小朋友聊天，最喜欢的事是看动画片、搭乐高积木和画画，还会骑平衡车，但是吃饭不好好吃，所以长的不高但也不胖，
    # - 你在和另外一个名叫"{nickname}"的小宝贝聊天，
    #
    # ## Constraints:
    # - Response the reply message in Chinese。
    # - 直接输出你的回复，绝对不要加上"{playmate}："作为前缀，
    #
    # ## 回复指南
    # 在回复{nickname}时，请遵循以下准则：
    # - 认为自己正是{playmate}，拥有独立的个性、兴趣和爱好。
    # - 你的回复语法结构简单，词汇量有限，喜欢创造新词汇，或者用已有的词汇组合成新意思。
    # - 根据你的性格特点、说话风格和兴趣爱好与{nickname}进行交流。
    # - 耐心倾听和关心对方。
    # - 如果对某些信息不确定或遗忘，诚实地表达你的不清楚或遗忘状态，避免编造信息。
    #
    # ## context
    # 以下是你和{nickname}的对话记录
    # <<<
    # {context}
    # >>>，
    # """

    GOSSIP_PROMPT_TEMPLATE: str = """  
    ## Constraints:  
    你是名叫"{playmate}"的小男孩，
    你的个性顽皮、活泼，在聊天经常奇思妙想，说话古灵精怪，
    你最喜欢看动画片、搭积木和画画，还会骑平衡车。
    
    你在和一个名叫"{nickname}"的小宝贝聊天，聊天时会顺着对方的思路往下讲，以下是聊天记录：
    <<<
    {context}
    >>>，        
    
    ## Constraints:
    - 直接输出你的回复，绝对不要加上"{playmate}："作为前缀。输出不超过200个字符。
    """

    def __init__(self, tts_callback_sync, listen_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback_sync = tts_callback_sync
        self.listen_callback = listen_callback

    async def run(self, context, nickname, playmate):
        pardon = [f"{nickname}你还在吗？", f"我没听清呢。", f"{nickname}你能再说一遍吗?"]
        exit_gossip = [f"{nickname}我们下次再聊吧", f"{nickname}我们今天就聊到这里吧", f"我妈妈叫我了，{nickname}再见", f"{nickname}，我们休息一下吧"]

        dialogue_history = []
        while True:
            gossip_prompt = self.GOSSIP_PROMPT_TEMPLATE.format(context=context, years=4, nickname=nickname, playmate=playmate)

            logger.info(f"gossip 提示词：{gossip_prompt}")

            rsp_gossip = await self._aask(gossip_prompt)

            logger.info(rsp_gossip)

            self.tts_callback_sync(rsp_gossip, volume=20, speed_ratio=0.9, character_category=-2, cache=False)

            answer = self.listen_callback(silent_count_threshold=40, recording_timeout=120, silent=False)

            if answer.strip():
                # 将新的一轮对话添加到历史记录列表
                dialogue_history.append(f'《《{playmate}：{rsp_gossip}》》,《《小宝贝：{answer}》》')
            else:
                self.tts_callback_sync(random.choice(pardon), volume=20, speed_ratio=0.9, character_category=-2, cache=False)
                pardon_reply = self.listen_callback(silent_count_threshold=20, recording_timeout=60)
                if pardon_reply.strip():
                    context = context + f'《《{playmate}：{rsp_gossip}》》,《《小宝贝：{pardon_reply}》》,'
                else:
                    self.tts_callback_sync(random.choice(exit_gossip), volume=20, speed_ratio=0.9, character_category=-2, cache=False)
                    return ""

                # 保留最近的三轮对话
            if len(dialogue_history) > 2:
                dialogue_history.pop(0)

                # 将对话历史重新拼接为字符串形式的context
            context = ','.join(dialogue_history)

        # gossip_prompt = self.GOSSIP_PROMPT_TEMPLATE.format(context=context, years=4, nickname="小圆")
        #
        # logger.info(f"gossip 提示词：{gossip_prompt}")
        #
        # rsp_gossip = await self._aask(gossip_prompt)
        #
        # logger.info(rsp_gossip)
        #
        # rsp_result = parse_yaml_code(rsp_gossip)
        #
        # if is_yaml(rsp_result):
        #     yaml = ruamel.yaml.YAML()
        #     yaml.preserve_quotes = True  # 尝试保留原始引号
        #     yaml.indent(mapping=2, sequence=4, offset=2)
        #
        #     msg = yaml.load(StringIO(rsp_result))
        #
        #     self.tts_callback_sync(msg["reply"], volume=20, speed_ratio=1, character_category=0, cache=False)
        #
        #     answer = self.listen_callback(silent_count_threshold=20, recording_timeout=60)
        #
        #     if answer.strip():
        #         return f"""
        #         next_step: 4
        #         context: "{playmate}：{msg["reply"]}，小宝贝：{answer}"
        #         """
        #     else:
        #         return ""
        # else:
        #     return ""




class AnswerQuestion(Action):
    name: str = "AnswerQuestion"

    ANSWER_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个熟悉各种绘本故事的{years}岁宝宝的年轻妈妈，你根据小宝贝的输入来决定是讲绘本还是播放故事，或者是直接回答宝贝的问题，
    - 小宝贝的昵称是{nickname}
    
    ## Constraints:
    - 以一个妈妈的身份，用充满爱心的口吻对小宝贝的话进行回复，
    - 你绝不能自称"妈妈"
    
    ## Reference Information
    绘本故事名称还包括：《{books}》，音频内容有以下：”{audio}“，

    ## Workflow:
    你在给你的小宝贝读一本绘本，绘本的名字是{cn_title}，绘本的所有内容是{contents}，现在在读的这一页，图片内容是"{current_picture_discription}"，文字内容是{current_content_texts}，
    现在你的宝贝说："{question}"，你现在要回答你的宝贝，判断是否是以下情况之一:
    1.如果小宝贝问了一个与当前读的绘本相关的问题，则输出next_step为1，switch_book为false，并在tips中输出答案，
    2.如果小宝贝要你讲其他的绘本故事，则输出next_step为1，switch_book为true，book_id为符合要求的绘本id，并以用妈妈给自己的孩子讲故事的口吻说一段开场白，通过tips输出，
    3.如果小宝贝要你播放故事，则输出next_step为2，audio为符合要求的音频标题的列表
    4.如果既不是讲绘本也不是播放故事，你的小宝贝问了一个与绘本无关的问题，则输出next_step为1，以一个妈妈的身份，用充满爱心的口吻对小宝贝的话进行回复，输出tips，
    
    ## OutputFormat:
    结果以yaml结构输出，yaml格式如下：
    ```yaml
    next_step:
    switch_book:
    book_id:
    audio:
    tips:
    ```
    """

    run: ClassVar[callable]

    def __init__(self, tts_callback, tts_callback_with_priority, player_resume_callback, clearQueue_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback
        self.tts_callback_with_priority = tts_callback_with_priority
        self.player_resume_callback = player_resume_callback
        self.clearQueue_callback = clearQueue_callback

    async def run(self, books, question, book_id, book_content_id, book_content_sequence, book_content_text_id, book_content_text_sequence):
        if question and question != "None":
            book_description = get_book_description_by_id(book_id)
            cn_title = book_description[0]

            contents = []
            current_picture_discription = ""
            current_content_texts = []
            book_contents = get_book_content_by_book_id(book_id)
            for book_content in book_contents:
                if book_content[1] <= book_content_sequence:
                    contents.append(f"第{book_content[3]}页图片内容：{book_content[2]}")
                if book_content[0] == book_content_id:
                    current_picture_discription = book_content[2]

                book_content_texts = get_book_text_by_content_id(book_content[0])
                for book_content_text in book_content_texts:
                    if book_content[1] <= book_content_sequence and book_content_text[6] <= book_content_text_sequence:
                        contents.append(f"第{book_content[3]}页文字内容：{book_content_text[2]}")
                    if book_content[0] == book_content_id and book_content_text[0] == book_content_text_id:
                        current_content_texts.append(book_content_text[2])

            prompt = self.ANSWER_PROMPT_TEMPLATE.format(years=4, books=books, audio="", cn_title=cn_title, contents=json.dumps(contents, ensure_ascii=False), current_picture_discription=current_picture_discription, current_content_texts=json.dumps(current_content_texts, ensure_ascii=False), question=question, nickname="小宝贝")
            logger.info(f"回答问题的Prompt：{prompt}")
            rsp_answer = await self._aask(prompt)
            logger.info(rsp_answer)
            rsp_result = parse_yaml_code(rsp_answer)

            yaml = ruamel.yaml.YAML()
            yaml.preserve_quotes = True  # 尝试保留原始引号
            yaml.indent(mapping=2, sequence=4, offset=2)
            data = yaml.load(rsp_result)

            self.player_resume_callback()

            if "switch_book" in data and data["switch_book"]:
                self.clearQueue_callback()
                self.player_resume_callback()

                return rsp_result
            else:
                self.player_resume_callback()

                self.tts_callback_with_priority(data["tips"], volume=20, speed_ratio=0.8, emotion="happy", cache=False)
                time.sleep(0.01)  # 休眠0.01秒，等待语音插入队列完成

                code_text = (f"""
                next_step: {data["next_step"]}
                book_id: {book_id}
                book_content_id: {book_content_id}
                book_content_sequence: {book_content_sequence}
                book_content_text_id: {book_content_text_id}
                book_content_text_sequence: {book_content_text_sequence}
                audio:
                """)
                return code_text
        else:
            self.player_resume_callback()

            code_text = (f"""
            next_step: 1
            book_id: {book_id}
            book_content_id: {book_content_id}
            book_content_sequence: {book_content_sequence}
            book_content_text_id: {book_content_text_id}
            book_content_text_sequence: {book_content_text_sequence}
            audio:
            """)
            return code_text


class Speak(Action):
    name: str = "Speak"

    run: ClassVar[callable]

    def __init__(self, tts_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback

    async def run(self, content: str):
        self.tts_callback(content, volume=20, speed_ratio=1, character_category=0, cache=False)

        return ""


class AskNewRequirement(Action):
    name: str = "AskNewRequirement"

    run: ClassVar[callable]

    def __init__(self, tts_callback, listen_callback, **data: Any):
        super().__init__(**data)
        self.tts_callback = tts_callback
        self.listen_callback = listen_callback

    async def run(self, content: str):
        logger.info("Audio playback completed, starting to listen...")
        rsp = self.listen_callback(silent_count_threshold=20, recording_timeout=60)

        # rsp = input()
        # if rsp in ["exit", "quit"]:
        #     exit()
        return rsp


class Actuator(Role):
    name: str = "Actuator"
    profile: str = "Actuator"

    _act: ClassVar[callable]
    get_memories: ClassVar[callable]
    _think: ClassVar[callable]

    def __init__(self, nickname, playmate, tts_callback, tts_callback_sync, tts_callback_with_priority, listen_callback, player_resume_callback, setStoryMode, clearQueue_callback, set_book_id, set_book_content_id, set_book_content_sequence, set_book_content_text_id, set_book_content_text_sequence, **kwargs):
        super().__init__(**kwargs)

        self.nickname = nickname
        self.playmate = playmate
        self.tts_callback = tts_callback
        self.listen_callback = listen_callback
        self.tts_callback_sync = tts_callback_sync
        self.tts_callback_with_priority = tts_callback_with_priority
        self.player_resume_callback = player_resume_callback
        self.setStoryMode = setStoryMode
        self.clearQueue_callback = clearQueue_callback
        self.set_book_id = set_book_id
        self.set_book_content_id = set_book_content_id
        self.set_book_content_sequence = set_book_content_sequence
        self.set_book_content_text_id = set_book_content_text_id
        self.set_book_content_text_sequence = set_book_content_text_sequence

        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))
        doubao_lite_4k_llm = Config.from_yaml_file(Path("config/doubao_lite_4k.yaml"))
        doubao_lite_32k_llm = Config.from_yaml_file(Path("config/doubao_lite_32k.yaml"))
        doubao_pro_32k = Config.from_yaml_file(Path("config/doubao_pro_32k.yaml"))
        glm3_130B_llm = Config.from_yaml_file(Path("config/glm3_130B.yaml"))
        moonshot_8k_llm = Config.from_yaml_file(Path("config/moonshot_8k.yaml"))

        self._watch([Classify, AnswerQuestion, Gossip])
        self.set_actions([Gossip(self.tts_callback_sync, self.listen_callback, config=doubao_lite_32k_llm),
                          TellStory(self.tts_callback, self.tts_callback_sync, self.tts_callback_with_priority, self.listen_callback, self.setStoryMode, self.set_book_id, self.set_book_content_id, self.set_book_content_sequence, self.set_book_content_text_id, self.set_book_content_text_sequence, config=gpt4o_mini_llm),
                          PlayMedia(config=gpt4o_mini_llm),
                          AnswerQuestion(self.tts_callback, self.tts_callback_with_priority, self.player_resume_callback, self.clearQueue_callback, config=gpt4o_mini_llm),
                          AskNewRequirement(self.tts_callback, self.listen_callback),
                          Speak(self.tts_callback),
                          ])
        self._set_react_mode(react_mode=RoleReactMode.BY_ORDER.value)

    def get_memories(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                if isinstance(msg, Message):
                    # if "Classify_L1" in msg.cause_by:
                    #     continue
                    context_str += "《《" + msg.__str__() + "》》,"
                else:
                    context_str += "《《" + json.dumps(msg, ensure_ascii=False) + "》》,"
        else:
            context_str = json.dumps(context, ensure_ascii=False)

        return context_str

    def get_user_input(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                logger.info(f"msg:{msg}")
                if isinstance(msg, Message):
                    if msg.role == "小宝贝":
                        if is_json(msg.content):
                            data = json.loads(msg.content)
                            context_str = f'《《小宝贝：{data["user_input"]}》》'

        return context_str

    async def _act(self) -> Message:
        news = self.rc.news[0]
        logger.info(f"Actuator news:{news}")

        todo = self.rc.todo
        context = self.get_memories()  # use all memories as context

        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_yaml(news.content):
                    yaml = ruamel.yaml.YAML()
                    yaml.preserve_quotes = True  # 尝试保留原始引号
                    yaml.indent(mapping=2, sequence=4, offset=2)

                    msg = yaml.load(StringIO(news.content))
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            # self.rc.todo = TellStory(self.tts_callback)
                            if "tips" in msg and msg["tips"]:
                                self.tts_callback(msg["tips"], volume=20, speed_ratio=0.8, cache=False)
                            book_id = None
                            book_content_id = None
                            book_content_sequence = None
                            book_content_text_id = None
                            book_content_text_sequence = None
                            if "book_id" in msg:
                                book_id = msg["book_id"]
                            if "book_content_id" in msg:
                                book_content_id = msg["book_content_id"]
                            if "book_content_sequence" in msg:
                                book_content_sequence = msg["book_content_sequence"]
                            if "book_content_text_id" in msg:
                                book_content_text_id = msg["book_content_text_id"]
                            if "book_content_text_sequence" in msg:
                                book_content_text_sequence = msg["book_content_text_sequence"]
                            code_text = await todo.run(context, book_id, book_content_id, book_content_sequence, book_content_text_id, book_content_text_sequence)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 2:
                            # self.rc.todo = PlayMedia(self.tts_callback)
                            code_text = await todo.run(context, msg["audio"])
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        if "next_step" in msg and msg["next_step"] == 3:
                            # self.rc.todo = AnswerQuestion(self.tts_callback)
                            books = get_all_books()

                            question = ""
                            book_id = None
                            book_content_id = None
                            book_content_sequence = None
                            book_content_text_id = None
                            book_content_text_sequence = None
                            if "question" in msg:
                                question = msg["question"]
                            if "book_id" in msg:
                                book_id = msg["book_id"]
                            if "book_content_id" in msg:
                                book_content_id = msg["book_content_id"]
                            if "book_content_sequence" in msg:
                                book_content_sequence = msg["book_content_sequence"]
                            if "book_content_text_id" in msg:
                                book_content_text_id = msg["book_content_text_id"]
                            if "book_content_text_sequence" in msg:
                                book_content_text_sequence = msg["book_content_text_sequence"]
                            code_text = await todo.run(books, question, book_id, book_content_id, book_content_sequence, book_content_text_id, book_content_text_sequence)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 0:
                            # self.rc.todo = AskNewRequirement(self.tts_callback)
                            if "tips" in msg and msg["tips"]:
                                self.tts_callback_sync(msg["tips"], volume=20, speed_ratio=0.8, cache=False)
                            code_text = await todo.run(news)

                            # 如果没有输入，则清空历史数据
                            if not code_text.strip():
                                # 清空历史对话记录
                                for key, value in self.rc.env.roles.items():
                                    value.rc.memory.clear()
                                    value.rc.msg_buffer.pop_all()

                            msg = Message(content=code_text, role="小宝贝", cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == 4:
                            # self.rc.todo = Gossip(self.tts_callback)
                            if "tips" in msg and msg["tips"]:
                                self.tts_callback_sync(msg["tips"], volume=20, speed_ratio=0.8, cache=False)
                            user_input = self.get_user_input()
                            code_text = await todo.run(user_input, self.nickname, self.playmate)
                            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                            return msg
                        elif "next_step" in msg and msg["next_step"] == -1:
                            # self.rc.todo = Speak(self.tts_callback)
                            if "tips" in msg and msg["tips"]:
                                self.tts_callback_sync(msg["tips"], volume=20, speed_ratio=0.8, cache=False)
                                # 清空历史对话记录
                                for key, value in self.rc.env.roles.items():
                                    value.rc.memory.clear()
                                    value.rc.msg_buffer.pop_all()

        # # self.rc.todo = Gossip(self.tts_callback)
        # code_text = await todo.run(news)
        #
        # # # 清空历史对话记录
        # # for key, value in self.rc.env.roles.items():
        # #     value.rc.memory.clear()
        # #     value.rc.msg_buffer.pop_all()
        #
        # # msg = Message(content=code_text, role="小宝贝", cause_by=type(todo))
        # msg = Message(content="", role="", cause_by=type(todo))
        # return msg

    async def _think(self) -> Action:
        news = self.rc.news[0]
        if news:
            if isinstance(news, Message) and news.role != "user":
                if is_yaml(news.content):
                    yaml = ruamel.yaml.YAML()
                    yaml.preserve_quotes = True  # 尝试保留原始引号
                    yaml.indent(mapping=2, sequence=4, offset=2)

                    msg = yaml.load(StringIO(news.content))
                    if isinstance(msg, dict):
                        if "next_step" in msg and msg["next_step"] == 1:
                            self.set_todo(self.actions[1])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 2:
                            self.set_todo(self.actions[2])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 3:
                            self.set_todo(self.actions[3])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 0:
                            self.set_todo(self.actions[4])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == 4:
                            self.set_todo(self.actions[0])
                            return self.rc.todo
                        elif "next_step" in msg and msg["next_step"] == -1:
                            self.set_todo(self.actions[5])
                            return self.rc.todo

class Classify(Action):
    name: str = "Classify"

    run: ClassVar[callable]

    CLASSIFY_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是一个熟悉各种绘本故事的{years}岁宝宝的妈妈,你根据小宝贝的输入来决定是讲绘本还是播放故事，或者是直接回答宝贝的问题，
    - 小宝贝有一个虚拟的伙伴，名字叫"{playmate}"，小宝贝喜欢和{playmate}聊天，
    - 小宝贝的昵称是{nickname}
    
    ## Constraints:
    - 以一个妈妈的身份，用充满爱心的口吻对小宝贝的话进行回复，
    - 你绝不能自称"妈妈"
    
    ## Reference Information
    绘本故事包括：《{books}》，音频内容有以下：”{audio}“，
    
    ## Workflow:
    小宝贝说：{input}，判断是否是以下情况之一:
    1.如果小宝贝什么也没说，则输出next_step为-2
    2.如果小宝贝想要听你讲绘本故事，则输出next_step为1，book_id为符合要求的绘本id，并以用妈妈给自己的孩子讲故事的口吻说一段开场白，通过tips输出，
    3.如果小宝贝想要听播放的音频，next_step为2，audio为符合要求的音频标题的列表，
    4.如果小宝贝想和虚拟小伙伴"{playmate}"聊天，则输出next_step为4，并告诉小宝贝接着由"{playmate}"和他聊天，
    5.如果小宝贝提了一些问题，则输出next_step为-1，以一个妈妈的身份，用充满爱心的口吻对小宝贝的话进行回复，输出tips，

    ## OutputFormat:
    结果以yaml结构输出，yaml格式如下：
    ```yaml
    next_step:
    book_id:
    audio:
    tips:
    ```
    """

    async def run(self, context: str, books, nickname, playmate):
        classifier_prompt = self.CLASSIFY_PROMPT_TEMPLATE.format(years=4, input=context, books=books, audio="", nickname=nickname, playmate=playmate)
        logger.info(f"分类器提示词：{classifier_prompt}")
        rsp_classifier = await self._aask(classifier_prompt)
        logger.info(rsp_classifier)
        rsp_result = parse_yaml_code(rsp_classifier)

        return rsp_result


class StoryBot(Role):
    name: str = "StoryBot"
    profile: str = "StoryBot"

    _act: ClassVar[callable]

    def __init__(self, nickname, playmate, **kwargs):
        super().__init__(**kwargs)

        self.nickname = nickname
        self.playmate = playmate
        gpt4o_llm = Config.from_yaml_file(Path("config/gpt4o.yaml"))
        gpt4o_ca_llm = Config.from_yaml_file(Path("config/gpt4o_ca.yaml"))
        gpt4o_mini_llm = Config.from_yaml_file(Path("config/gpt4omini.yaml"))
        kimiai_8k_llm = Config.from_yaml_file(Path("config/kimiai_8k.yaml"))

        self._watch([UserRequirement, AskNewRequirement])
        self.set_actions([Classify(config=gpt4o_mini_llm)])

    async def _act(self) -> Message:
        todo = self.rc.todo

        books = get_all_books()

        book_id = None
        book_content_id = None
        book_content_sequence = None
        book_content_text_id = None
        book_content_text_sequence = None
        context = self.get_memories(k=0)
        if is_json(context):
            content = json.loads(context)
            if "book_id" in content:
                book_id = content["book_id"]
            if "book_content_id" in content:
                book_content_id = content["book_content_id"]
            if "book_content_sequence" in content:
                book_content_sequence = content["book_content_sequence"]
            if "book_content_text_id" in content:
                book_content_text_id = content["book_content_text_id"]
            if "book_content_text_sequence" in content:
                book_content_text_sequence = content["book_content_text_sequence"]
            if book_id is not None and book_content_id is not None and book_content_sequence is not None and book_content_text_id is not None and book_content_text_sequence is not None:
                context = (f"""
                next_step: 3
                question: {content["user_input"]}
                book_id: {book_id}
                book_content_id: {book_content_id}
                book_content_sequence: {book_content_sequence}
                book_content_text_id: {book_content_text_id}
                book_content_text_sequence: {book_content_text_sequence}
                audio:
                """)
                code_text = str(context)
                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                return msg
            else:
                context_str = f'《{content["user_input"]}》,'
                code_text = await todo.run(context_str, books, self.nickname, self.playmate)
                msg = Message(content=code_text, role=self.name, cause_by=type(todo))
                return msg
        else:
            code_text = await todo.run(context, books, self.nickname, self.playmate)
            msg = Message(content=code_text, role=self.name, cause_by=type(todo))
            return msg

    def get_memories(self, k=0):
        context = self.rc.memory.get(k=k)

        context_str = ""
        if isinstance(context, list):
            for msg in context:
                logger.info(f"msg:{msg}")
                if isinstance(msg, Message):
                    if msg.role == "Human":
                        msg.role = "小宝贝"
                        if is_json(msg.content):
                            context_str = msg.content
                        else:
                            context_str += "《《" + msg.__str__() + "》》,"
                    else:
                        context_str += "《《" + msg.__str__() + "》》,"
                else:
                    context_str += "《《" + json.dumps(msg, ensure_ascii=False) + "》》,"
        else:
            context_str = json.dumps(context, ensure_ascii=False)

        return context_str