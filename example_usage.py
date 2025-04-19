from llm_api_framework.core.manager import APIManager
from PIL import Image

def format_response(response):
    """格式化API响应，添加<think>标签"""
    output = ""
    if response["error"]:
        return f"错误: {response['error']}"
    
    if response["reasoning_content"]:
        output += f"<think>\n{response['reasoning_content']}</think>\n"
    if response["content"]:
        output += response["content"]
    return output

def test_vision_model():
    # 使用Qwen2.5-VL-7B-Instruct视觉模型
    manager = APIManager("configs/qwen2.5-VL-7B-Instruct.json")
    
    # 添加视觉消息
    image_url = "/home/kzer/screencut/1730277332445.png"
    image_data = Image.open(image_url)
    manager.add_vision_message("user", [image_data])
    
    print("测试视觉模型同步调用:")
    response = manager.call_api("描述这幅图")
    print(format_response(response))
    
    # 保存对话历史
    print("\n\n完整对话历史:")
    for msg in manager.get_conversation_history():
        if isinstance(msg["content"], list):
            print(f"{msg['role']}: [视觉消息]")
        else:
            print(f"{msg['role']}: {msg['content']}")

if __name__ == "__main__":
    test_vision_model()