from llm_api_framework.core.manager import APIManager
import time

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

def test_reasoning_model():
    # 使用deepseek-R1-Distill-Qwen-7B模型
    manager = APIManager("configs/deepseek-R1-Distill-Qwen-7B.json")
    
    print("测试同步调用:")
    response = manager.call_api("请解释量子计算的基本原理")
    print(format_response(response))
    
    print("\n测试流式调用:")
    full_response = ""
    for chunk in manager.stream_api("请用简单的语言解释相对论"):
        if chunk["error"]:
            print(f"错误: {chunk['error']}")
            break
        
        formatted = format_response(chunk)
        print(formatted, end="", flush=True)
        if chunk["content"]:
            full_response += chunk["content"]
    
    # 保存对话历史
    print("\n\n完整对话历史:")
    for msg in manager.get_conversation_history():
        print(f"{msg['role']}: {msg['content']}")

if __name__ == "__main__":
    test_reasoning_model()
