from llm_api_framework.core.session import LLMSession
from PIL import Image

def format_response(response):
    """格式化API响应，添加<think>标签"""
    output = ""
    if response.get("error"):
        return f"错误: {response['error']}"
    
    if response.get("reasoning_content"):
        output += f"<think>\n{response['reasoning_content']}\n</think>\n"
    if response.get("content"):
        output += response["content"]
    return output

def test_vision_model():
    # 使用Qwen2.5-VL-7B-Instruct视觉模型
    # session = LLMSession("configs/qwen2.5-VL-72B-Instruct.json")
    # session = LLMSession("configs/deepseek-R1-Distill-Qwen-7B.json")
    session = LLMSession("configs/deepseek-v3-0324.json")
    
    
    # 添加视觉消息
    # 使用网络图片或本地图片
    image_url = "https://modelscope.oss-cn-beijing.aliyuncs.com/demo/images/audrey_hepburn.jpg"
    # session.add_vision_message("user", [image_url])

    print(format_response(session.call_api("做家务的步骤有哪些")))
    # print(format_response(session.call_api("描述这张图片")))
    
    print("测试模型流式调用:")
    full_response = ""
    for chunk in session.stream_api("具体到厨务呢"):
    # for chunk in session.stream_api("有可能发生在哪个国家"):
        formatted = format_response(chunk)
        print(formatted, end="", flush=True)
        content = chunk.get("content") or chunk.get("reasoning_content")
        if content:
            full_response += content
    
    # 保存对话历史
    print("\n\n完整对话历史:")
    for msg in session.get_conversation_history():
        if isinstance(msg["content"], list):
            print(f"{msg['role']}: [视觉消息]")
        else:
            print(f"{msg['role']}: {msg['content']}")

if __name__ == "__main__":
    test_vision_model()