from crewai_tools import NL2SQLTool


nl2sql = NL2SQLTool(
    db_uri=r"sqlite:///C:/Users/Nitisha/OneDrive/Documents/VSCode/agriculture_mcp/database/agriculture.db"
)