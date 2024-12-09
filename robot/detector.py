import time

from snowboy import snowboydecoder
import azure.cognitiveservices.speech as speechsdk
from robot import config, logging, utils, constants

logger = logging.getLogger(__name__)

azure_detector = None
detector = None
recorder = None
porcupine = None


def initDetector(wukong):
    """
    初始化离线唤醒热词监听器，支持 snowboy 和 porcupine 两大引擎
    """
    global porcupine, recorder, detector, azure_detector
    if config.get("detector", "snowboy") == "porcupine":
        logger.info("使用 porcupine 进行离线唤醒")

        import pvporcupine
        from pvrecorder import PvRecorder

        access_key = config.get("/porcupine/access_key")
        keyword_paths = config.get("/porcupine/keyword_paths")
        keywords = config.get("/porcupine/keywords", ["porcupine"])
        if keyword_paths:
            porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=[constants.getConfigData(kw) for kw in keyword_paths],
                sensitivities=[config.get("sensitivity", 0.5)] * len(keyword_paths),
            )
        else:
            porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=keywords,
                sensitivities=[config.get("sensitivity", 0.5)] * len(keywords),
            )

        recorder = PvRecorder(device_index=-1, frame_length=porcupine.frame_length)
        recorder.start()

        try:
            while True:
                pcm = recorder.read()

                result = porcupine.process(pcm)
                if result >= 0:
                    kw = keyword_paths[result] if keyword_paths else keywords[result]
                    logger.info(
                        "[porcupine] Keyword {} Detected at time {}".format(
                            kw,
                            time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(time.time())
                            ),
                        )
                    )
                    wukong._detected_callback(False)
                    recorder.stop()
                    wukong.conversation.interrupt()
                    query = wukong.conversation.activeListen()
                    wukong.conversation.doResponse(query)
                    recorder.start()
        except pvporcupine.PorcupineActivationError as e:
            logger.error("[Porcupine] AccessKey activation error", stack_info=True)
            raise e
        except pvporcupine.PorcupineActivationLimitError as e:
            logger.error(
                f"[Porcupine] AccessKey {access_key} has reached it's temporary device limit",
                stack_info=True,
            )
            raise e
        except pvporcupine.PorcupineActivationRefusedError as e:
            logger.error(
                "[Porcupine] AccessKey '%s' refused" % access_key, stack_info=True
            )
            raise e
        except pvporcupine.PorcupineActivationThrottledError as e:
            logger.error(
                "[Porcupine] AccessKey '%s' has been throttled" % access_key,
                stack_info=True,
            )
            raise e
        except pvporcupine.PorcupineError as e:
            logger.error("[Porcupine] 初始化 Porcupine 失败", stack_info=True)
            raise e
        except KeyboardInterrupt:
            logger.info("Stopping ...")
        finally:
            porcupine and porcupine.delete()
            recorder and recorder.delete()

    elif config.get("detector", "snowboy") == "snowboy":
        logger.info("使用 snowboy 进行离线唤醒")
        detector and detector.terminate()
        models = constants.getHotwordModel(config.get("hotword", "wukong.pmdl"))
        detector = snowboydecoder.HotwordDetector(
            models, sensitivity=config.get("sensitivity", 0.5)
        )
        # main loop
        try:
            callbacks = wukong._detected_callback
            detector.start(
                detected_callback=callbacks,
                audio_recorder_callback=wukong.conversation.converse,
                interrupt_check=wukong._interrupt_callback,
                silent_count_threshold=config.get("silent_threshold", 15),
                recording_timeout=config.get("recording_timeout", 5) * 4,
                sleep_time=0.03,
            )
            detector.terminate()
        except Exception as e:
            logger.critical(f"离线唤醒机制初始化失败：{e}", stack_info=True)

    elif config.get("detector", "azure") == "azure":
        logger.info("使用 azure 进行离线唤醒")

        speech_key = config.get("/azure/speech_key")
        service_region = config.get("/azure/service_region")

        model = constants.getHotwordModel(config.get("/azure/model"))
        keyword_model = speechsdk.KeywordRecognitionModel(model)

        keyword_recognizer = speechsdk.KeywordRecognizer()

        sleep_time = 0.03

        done = False

        def recognized_cb(evt):
            # Only a keyword phrase is recognized. The result cannot be 'NoMatch'
            # and there is no timeout. The recognizer runs until a keyword phrase
            # is detected or recognition is canceled (by stop_recognition_async()
            # or due to the end of an input file or stream).
            result = evt.result
            if result.reason == speechsdk.ResultReason.RecognizedKeyword:
                print("RECOGNIZED KEYWORD: {}".format(result.text))
            nonlocal done
            done = True

        def canceled_cb(evt):
            result = evt.result
            if result.reason == speechsdk.ResultReason.Canceled:
                print('CANCELED: {}'.format(result.cancellation_details.reason))
            nonlocal done
            done = True

        try:
            while True:
                # Connect callbacks to the events fired by the keyword recognizer.
                keyword_recognizer.recognized.connect(recognized_cb)
                keyword_recognizer.canceled.connect(canceled_cb)

                # Start keyword recognition.
                result_future = keyword_recognizer.recognize_once_async(keyword_model)

                result = result_future.get()

                if not result:
                    time.sleep(sleep_time)
                    return False

                if result.reason == speechsdk.ResultReason.RecognizedKeyword:
                    if not utils.is_proper_time():
                        logger.warning("勿扰模式开启中")
                        return
                    if wukong.conversation.isRecording:
                        logger.warning("正在录音中，跳过")
                        return
                    # wukong._detected_callback(False)
                    wukong.conversation.interrupt()
                    query = wukong.conversation.activeListen(silent_count_threshold=20, recording_timeout=60)
                    wukong.conversation.doResponse(query)

        except Exception as e:
            logger.error(f"An error occurred: {e}", exc_info=True)