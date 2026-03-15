from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from sonar_agent.core import config


def get_langchain_llm():
    """Returns the most appropriate configured LangChain LLM model based on API keys."""
    # Priority order for this project: Anthropic (Claude 3.5 Sonnet) -> Groq (Llama 3.3) -> Gemini -> OpenAI
    
    if getattr(config, "ANTHROPIC_API_KEY", None):
        return ChatAnthropic(
            model="claude-3-5-sonnet-20241022", 
            temperature=0, 
            api_key=config.ANTHROPIC_API_KEY
        )
        
    if getattr(config, "GROQ_API_KEY", None):
        return ChatGroq(
            model="llama-3.3-70b-versatile", 
            temperature=0, 
            api_key=config.GROQ_API_KEY
        )
        
    if getattr(config, "GEMINI_API_KEY", None):
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-preview-05-20", 
            temperature=0, 
            api_key=config.GEMINI_API_KEY
        )
        
    if getattr(config, "OPENAI_API_KEY", None):
        return ChatOpenAI(
            model="gpt-4o", 
            temperature=0, 
            api_key=config.OPENAI_API_KEY
        )
        
    raise ValueError("No LLM API keys configured!")
