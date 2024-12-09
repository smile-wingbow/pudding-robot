import json

from flask import Flask, render_template, request
import os
from PIL import Image
import io
from io import StringIO
import base64
import asyncio
import re
import sqlite3
import uuid
import ruamel.yaml
import shutil
import cv2
from pytesseract import pytesseract
from pytesseract import Output
from concurrent.futures import ThreadPoolExecutor, as_completed

from metagpt.actions import Action
from metagpt.roles.role import Role, RoleReactMode
from metagpt.schema import Message
from metagpt.logs import logger

# pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# 创建一个全局的线程池，限制最大并发线程数
executor = ThreadPoolExecutor(max_workers=5)

# 创建连接
conn = sqlite3.connect('./story_db/picture_books.db')
cursor = conn.cursor()

# 创建 t_picture_book 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS t_picture_book (
    id TEXT PRIMARY KEY,
    cn_title TEXT NOT NULL,
    cn_subtitle TEXT,
    en_title TEXT,
    en_subtitle TEXT,
    picture_content TEXT
)
''')

# 创建 t_picture_book_content 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS t_picture_book_content (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,    
    picture_content TEXT NOT NULL,
    FOREIGN KEY(book_id) REFERENCES t_picture_book(id)
)
''')

# 创建 t_picture_book_text 表
cursor.execute('''
CREATE TABLE IF NOT EXISTS t_picture_book_text (
    id TEXT PRIMARY KEY,
    content_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    language INTEGER NOT NULL,
    text TEXT NOT NULL,
    type INTEGER NOT NULL,
    character TEXT,
    character_category INTEGER NOT NULL,
    FOREIGN KEY(content_id) REFERENCES t_picture_book_content(id)
)
''')

cursor.close()
conn.commit()
conn.close()

# logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates')

# 配置文件上传路径
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def is_yaml(yaml_str):
    yaml = ruamel.yaml.YAML()
    try:
        yaml_object = yaml.load(StringIO(yaml_str))
        if not isinstance(yaml_object, dict):  # 检查是否为字典
            return False
    except Exception as e:
        return False
    return True


def parse_yaml_code(rsp):
    # 正则表达式匹配yaml代码块
    pattern = r"```yaml(.*?)```"
    match = re.search(pattern, rsp, re.DOTALL)

    if match:
        code_text = match.group(1).strip()
    else:
        # 如果没有完全匹配，尝试提取到第一个 ``` 结束
        pattern_partial = r"```yaml(.*)"
        match_partial = re.search(pattern_partial, rsp, re.DOTALL)
        if match_partial:
            code_text = match_partial.group(1).strip()
        else:
            code_text = rsp

    return code_text


class DescribePicture(Action):
    name: str = "DescribePicture"

    FRONT_COVER_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是语言大师，也是一个充满爱心的年轻妈妈，你擅长用满怀童趣的语言来描述绘本

    ## Workflow:
    think step by step，执行以下步骤：
    1.准确提取图片上的中文标题，输出{{cn_title}}，
    2.准确提取中文副标题，副标题是图片上比主标题的文字小一些、起补充说明作用的标题文字，输出{{cn_subtitle}}，
    3.如果有英文标题，则准确提取绘本上的英文标题-{{en_title}}和英文副标题-{{en_subtitle}}
    4.用妈妈给自己的孩子讲故事的口吻对对图片进行描述-picture_content。
    分析结果以yaml结构输出，yaml格式如下：
    ```yaml 
    cn_title:
    cn_subtitle：
    en_title:
    en_subtitle：
    picture_content: 
    ```
    """

    TEXT_CONTENT_PROMPT_TEMPLATE: str = """
    ## Profile:
    - Language:中文
    - 你是语言大师，也是一个充满爱心的年轻妈妈，你擅长用满怀童趣的语言来描述绘本

    ## Workflow:
    think step by step，执行以下步骤：
    1.准确提取图片上的中文文字，输出{{cn_text}}，文字包括两类：一.图片上的描述性文字，输出type=1，二.对话，图片上人物的对话，type=2，对话还需要输出人物Character和人物特征Character_Category，Character_Category的选项包括：
        - Little Girl – 小女孩
        - Little Boy – 小男孩
        - Young Woman – 年轻女性
        - Young Man – 年轻男性
        - Mature Woman – 成熟女性
        - Mature Man – 成熟男性
        - Elderly Woman – 老年女性
        - Elderly Man – 老年男性
    2.如果有英文文字，则准确提取绘本上的英文文字-{{en_text}}，文字包括两类：一.图片上的描述性文字，输出type=1，二.对话，图片上人物的对话，type=2，对话还需要输出人物Character和人物特征Character_Category，Character_Category的选项包括：
        - Little Girl – 小女孩
        - Little Boy – 小男孩
        - Young Woman – 年轻女性
        - Young Man – 年轻男性
        - Mature Woman – 成熟女性
        - Mature Man – 成熟男性
        - Elderly Woman – 老年女性
        - Elderly Man – 老年男性
    3.用妈妈给自己的孩子讲故事的口吻对对图片进行描述-picture_content。
    分析结果以yaml结构输出，yaml格式如下：
    ```yaml 
    cn_text:
        - text: "在一个安静的小镇上，太阳正在缓缓升起。"
          type: 1  # 描述文字
        - text: "早安，小朋友们！"
          type: 2  # 对话
          character_category: Little Girl  # 小女孩
    en_text:
        - text: "The sun was rising quietly over the small town."
          type: 1  # 描述文字
        - text: "Good morning, children!"
          type: 2  # 对话
          character_category: Little Girl  # 小女孩
    picture_content: "小朋友，这一页我们可以看到一个漂亮的日出，温暖的阳光洒在草地上，小镇上的房子静静地沐浴在金色的光芒中。"     
    ```
    """

    async def run(self, code_text: str, directory: str):
        # 创建游标
        conn_book = sqlite3.connect('./story_db/picture_books.db')
        cursor_book = conn_book.cursor()

        book_id = str(uuid.uuid4())

        yaml = ruamel.yaml.YAML()
        yaml.preserve_quotes = True  # 尝试保留原始引号
        yaml.indent(mapping=2, sequence=4, offset=2)

        file_names_list = code_text.split(', ')

        for file_name in file_names_list:
            base_name = os.path.splitext(file_name)[0]

            if base_name == "cover":
                print(f'Processing file: {file_name}')

                imgpath = os.path.join(directory, f'{file_name}')

                # 生成回复
                img_base64_list = [_img_to_base64(imgpath)]
                description = await self._aask(prompt=self.FRONT_COVER_PROMPT_TEMPLATE, images=img_base64_list)
                # description = """
                # ```yaml
                # cn_title: 玛德琳的营救
                # en_title: MADELINE'S RESCUE
                # picture_content: 封面上，夕阳的余晖洒在一座古老的建筑上，天空被染成了温暖的橙色。一个穿着蓝色外套的小女孩和她的朋友们正排成一列，跟随着一位穿着长袍的女士，旁边还有一只可爱的小狗在欢快地奔跑。这个画面充满了温馨和冒险的气息，仿佛在邀请小朋友们一起踏上奇妙的旅程。
                # ```
                # """
                yaml_str = parse_yaml_code(description)

                if yaml_str and is_yaml(yaml_str):  # 确保匹配到的内容存在
                    data = yaml.load(StringIO(yaml_str))

                    print(yaml_str)

                    book_data = {
                        "id": book_id,
                        "cn_title": data["cn_title"],
                        "cn_subtitle": data["cn_subtitle"],
                        "en_title": data["en_title"],
                        "en_subtitle": data["en_subtitle"],
                        "picture_content": data["picture_content"]
                    }
                    try:
                        cursor_book.execute('''
                            INSERT INTO t_picture_book (id, cn_title, cn_subtitle, en_title, en_subtitle, picture_content)
                            VALUES (:id, :cn_title, :cn_subtitle, :en_title, :en_subtitle, :picture_content)
                        ''', book_data)
                    except Exception as e:
                        print(f"处理文件 {file_name} 时发生错误: {e}")
                        conn_book.rollback()

                    logger.info(f"绘本内容：{description}")
                else:
                    print("未找到有效的 YAML 匹配")
            else:
                print(f'Processing file: {file_name}')

                imgpath = os.path.join(directory, file_name)

                # 生成回复
                img_base64_list = [_img_to_base64(imgpath)]
                description = await self._aask(prompt=self.TEXT_CONTENT_PROMPT_TEMPLATE, images=img_base64_list)
                # description = """
                # ```yaml
                # cn_text: 这位新学生，真是热情又聪明。
                # en_text: The new pupil was ever so helpful and clever.
                # picture_content: 在一个充满童趣的教室里，老师正在黑板上指着一只画着的小猫，给小朋友们上课。小朋友们坐在整齐的课桌前，认真地听讲。一个可爱的小狗坐在地上，用积木搭出了“CAT”这个单词，显得既聪明又调皮。整个画面充满了学习的乐趣和童真的氛围。
                # ```
                # """
                yaml_str = parse_yaml_code(description)
                if yaml_str and is_yaml(yaml_str):  # 确保匹配到的内容存在
                    data = yaml.load(StringIO(yaml_str))

                    print(yaml_str)

                    content_id = str(uuid.uuid4())
                    book_content_data = {
                        "id": content_id,
                        "book_id": book_id,
                        "sequence": int(base_name),
                        "picture_content": data["picture_content"]
                    }
                    try:
                        cursor_book.execute('''
                            INSERT INTO t_picture_book_content  (id, book_id, sequence, picture_content)
                            VALUES (:id, :book_id, :sequence, :picture_content)
                        ''', book_content_data)
                    except Exception as e:
                        print(f"处理文件 {file_name} 时发生错误: {e}")
                        conn_book.rollback()

                    if "cn_text" in data:
                        cn_text_list = data["cn_text"]
                        if cn_text_list:
                            i = 0
                            for cn_text in cn_text_list:
                                character = ""
                                if "character" in cn_text:
                                    character = cn_text["character"]

                                # 默认为- Young Woman – 年轻女性
                                category = 2
                                if "character_category" in cn_text:
                                    character_category = cn_text["character_category"]
                                    if "Little Girl" in character_category:
                                        category = 0
                                    elif "Little Boy" in character_category:
                                        category = 1
                                    elif "Young Woman" in character_category:
                                        category = 2
                                    elif "Young Man" in character_category:
                                        category = 3
                                    elif "Mature Woman" in character_category:
                                        category = 4
                                    elif "Mature Man" in character_category:
                                        category = 5
                                    elif "Elderly Woman" in character_category:
                                        category = 6
                                    elif "Elderly Man" in character_category:
                                        category = 7

                                text_id = str(uuid.uuid4())
                                text_data = {
                                    "id": text_id,
                                    "content_id": content_id,
                                    "sequence": i,
                                    "language": 1,
                                    "text": cn_text["text"],
                                    "type": cn_text["type"],
                                    "character": character,
                                    "character_category": category
                                }
                                try:
                                    cursor_book.execute('''
                                        INSERT INTO t_picture_book_text  (id, content_id, sequence, language, text, type, character, character_category)
                                        VALUES (:id, :content_id, :sequence, :language, :text, :type, :character, :character_category)
                                    ''', text_data)
                                except Exception as e:
                                    print(f"处理文件 {file_name} 时发生错误: {e}")
                                    conn_book.rollback()
                                i += 1

                        if "en_text" in data:
                            en_text_list = data["en_text"]
                            if en_text_list:
                                i = 0
                                for en_text in en_text_list:
                                    character = ""
                                    if "character" in en_text:
                                        character = cn_text["character"]

                                    # 默认为- Young Woman – 年轻女性
                                    category = 2
                                    if "character_category" in en_text:
                                        character_category = en_text["character_category"]
                                        if "Little Girl" in character_category:
                                            category = 0
                                        elif "Little Boy" in character_category:
                                            category = 1
                                        elif "Young Woman" in character_category:
                                            category = 2
                                        elif "Young Man" in character_category:
                                            category = 3
                                        elif "Mature Woman" in character_category:
                                            category = 4
                                        elif "Mature Man" in character_category:
                                            category = 5
                                        elif "Elderly Woman" in character_category:
                                            category = 6
                                        elif "Elderly Man" in character_category:
                                            category = 7

                                    text_id = str(uuid.uuid4())
                                    text_data = {
                                        "id": text_id,
                                        "content_id": content_id,
                                        "sequence": i,
                                        "language": 1,
                                        "text": en_text["text"],
                                        "type": en_text["type"],
                                        "character": character,
                                        "character_category": category
                                    }
                                    try:
                                        cursor_book.execute('''
                                            INSERT INTO t_picture_book_text  (id, content_id, sequence, language, text, type, character, character_category)
                                            VALUES (:id, :content_id, :sequence, :language, :text, :type, :character, :character_category)
                                        ''', text_data)
                                    except Exception as e:
                                        print(f"处理文件 {file_name} 时发生错误: {e}")
                                        conn_book.rollback()
                                    i += 1

                    logger.info(f"绘本内容：{description}")
                else:
                    print("未找到有效的 YAML 匹配")

        cursor_book.close()  # 关闭游标
        conn_book.commit()  # 提交事务
        conn_book.close()  # 关闭连接
        return ""


class Critic(Role):
    name: str = "Critic"
    profile: str = "Critic"

    def __init__(self, directory, **kwargs):
        super().__init__(**kwargs)
        self.directory = directory
        self.set_actions([DescribePicture])
        self._set_react_mode(react_mode=RoleReactMode.REACT.value)

    async def _act(self) -> Message:
        logger.info(f"{self._setting}: to do {self.rc.todo}({self.rc.todo.name})")
        todo = self.rc.todo

        msg = self.get_memories(k=1)[0]  # find the most k recent messages
        result = await todo.run(msg.content, self.directory)

        msg = Message(content=result, role=self.profile, cause_by=type(todo))
        self.rc.memory.add(msg)
        return msg


# 确保上传文件夹存在
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


@app.route('/')
def index():
    return render_template('index.html')


def _img_to_base64(img_path):
    img = Image.open(img_path)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes = img_bytes.getvalue()

    img_b64 = base64.b64encode(img_bytes).decode('utf8')
    return img_b64


def increase_resolution(image, scale_percent=200):
    # 获取原图像的尺寸
    width = int(image.shape[1] * scale_percent / 100)
    height = int(image.shape[0] * scale_percent / 100)
    # 调整图像大小
    resized_image = cv2.resize(image, (width, height), interpolation=cv2.INTER_CUBIC)
    return resized_image


def correct_image_orientation(image_path, language):
    image_path = os.path.abspath(image_path)
    # 读取图像
    image = cv2.imread(image_path)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scaled_image = increase_resolution(gray, scale_percent=200)

    try:
        # 使用pytesseract进行方向检测
        # chi_sim 指定中文简体的语言包
        osd = pytesseract.image_to_osd(scaled_image, lang=language, output_type=Output.DICT)

        # 获取检测到的旋转角度
        angle = osd['rotate']
        print(f"{image_path}检测到的旋转角度: {angle}")
    except pytesseract.TesseractError as e:
        print(f"Tesseract 错误: {str(e)}")

        preprocessed_image = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                   cv2.THRESH_BINARY, 11, 2)
        try:
            osd = pytesseract.image_to_osd(preprocessed_image, lang=language, output_type=Output.DICT)
            angle = osd['rotate']
            print(f"检测到的旋转角度: {angle}")
        except pytesseract.TesseractError as e:
            print(f"Tesseract 错误: {str(e)}")
            return None

    # 如果图片不为0度，进行相应的旋转
    if angle == 90:
        rotated_image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif angle == 180:
        rotated_image = cv2.rotate(image, cv2.ROTATE_180)
    elif angle == 270:
        rotated_image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        rotated_image = image  # 如果是0度，保持原样

    return rotated_image


def correct_image(file, directory, language):
    file_path = os.path.join(directory, file.filename)  # 保存文件到随机子目录

    # 调整图片方向
    rotated_image = correct_image_orientation(file_path, language)
    if rotated_image is not None:
        # 保存修正后的图片
        cv2.imwrite(file_path, rotated_image)


def extract_picture_task(files, directory, language):
    # 创建一个新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 1. 使用线程池并行执行 correct_image
        futures = []
        for file in files:
            future = executor.submit(correct_image, file, directory, language)
            futures.append(future)

        # 2. 等待 correct_image 的所有任务完成
        for future in as_completed(futures):
            future.result()  # 捕获异常

        # 3. 确保 correct_image 完成后再执行下面的异步任务
        role = Critic(directory)

        file_names = [file.filename for file in files]
        files_str = ', '.join(file_names)

        # 使用事件循环执行异步任务
        result = loop.run_until_complete(role.run(files_str))
        logger.info(result)

    except Exception as e:
        logger.error(f'处理图片时发生错误: {e}')

    finally:
        # 关闭事件循环
        loop.close()
        # 所有任务完成后，删除目录
        shutil.rmtree(directory)

    # # 先翻转图片
    # correct_image(file, directory, language)
    #
    # # 创建一个新的事件循环
    # loop = asyncio.new_event_loop()
    # asyncio.set_event_loop(loop)
    #
    # # 确保事件循环已经设置后再实例化 Critic
    # role = Critic(directory, book_id)
    # # files_str = json.dumps(file_list, ensure_ascii=False)
    # # logger.info(files_str)
    #
    # # 使用事件循环执行异步任务
    # result = loop.run_until_complete(role.run(file.filename))
    # logger.info(result)
    #
    # # 关闭事件循环
    # loop.close()


@app.route('/upload', methods=['POST'])
def upload_file():
    # 获取上传的所有文件
    files = request.files.getlist('files[]')  # 使用 getlist 来获取多个文件
    print(files)

    # 创建随机子目录
    random_fold = str(uuid.uuid4())
    unique_dir = f"./uploads/{random_fold}"
    os.makedirs(unique_dir, exist_ok=True)

    # 检测语言暂时支持：chi_sim和eng
    language = "chi_sim+eng"

    for file in files:
        if file:
            print(file)
            file_path = os.path.join(unique_dir, file.filename)  # 保存文件到随机子目录

            # 检查是否是图片文件
            if not file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                raise ValueError(f"文件格式不支持: {file.filename}")

            file.save(file_path)  # 保存文件到服务器目录

    # 使用线程池启动任务
    executor.submit(extract_picture_task, files, unique_dir, language)

    return '文件上传成功'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
