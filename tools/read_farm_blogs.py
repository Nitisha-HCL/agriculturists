from typing import Any

from crewai.tools import tool

str1 = """Chickpea cultivation can be affected by several challenges, particularly irregular rainfall and water stress during the flowering and pod-filling stages. Farmers may also face problems from pests such as pod borers and diseases such as wilt and root rot. Poor soil fertility, delayed sowing, and sudden changes in temperature can further reduce plant growth and yield. Limited access to quality seeds, timely pest-control measures, and irrigation can make it difficult for farmers to manage these risks effectively.
"""
str2 = """Cotton farmers often face difficulties due to pest infestations, especially bollworms, aphids, and whiteflies, which can significantly affect crop quality and productivity. Unpredictable rainfall and prolonged dry periods can create additional stress, particularly during flowering and boll development. High input costs for seeds, fertilizers, pesticides, and irrigation can also increase the financial burden on farmers. Fluctuations in market prices and challenges in obtaining timely and fair prices for harvested cotton add further uncertainty to cotton cultivation.
"""
str3 = """Rice cultivation is highly dependent on adequate and timely water availability, making it vulnerable to both drought and excessive rainfall. Waterlogging and flooding can damage crops, while insufficient irrigation can reduce growth and grain formation. Farmers may also encounter weeds, pests, and diseases such as blast and bacterial leaf blight. Rising costs of fertilizers, pesticides, labor, and irrigation, along with difficulties in accessing reliable weather information and appropriate agricultural advice, can further complicate rice production.
"""
def read_farm_blogs(crop_name: str) :
    """Read agricultural blog sources and return structured article data."""
    articles = {"chickpea": str1, "cotton": str2, "rice": str3}

    return articles.get(crop_name, "Crop information not found.")


from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class ReadFarmBlogsInput(BaseModel):
    crop: str = Field(..., description="The crop to search for")


class ReadFarmBlogsTool(BaseTool):
    name: str = "read_farm_blogs"
    description: str = "Search farm blogs for information about a specific crop."
    args_schema: type[BaseModel] = ReadFarmBlogsInput

    def _run(self, crop: str) -> str:
        try:
            res = read_farm_blogs(crop)
            return res
        except Exception as e:
            return f"Error occurred while searching farm blogs for {crop}: {e}" 


if __name__ == "__main__":
    tool = ReadFarmBlogsTool()
    print(tool.run(crop="rice"))