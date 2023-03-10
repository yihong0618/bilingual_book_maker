from openai.embeddings_utils import get_embedding, cosine_similarity
import pandas as pd
from openai.embeddings_utils import get_embedding
import os

def read_terminology(terminology_filename):
    if not os.path.exists(terminology_filename):
        return None
    with open(terminology_filename, 'r', encoding='utf-8') as f:
        terminology_list=f.readlines()
    # 如果terminology_list为空，则返回None
    if len(terminology_list)==0:
        return None
    df=pd.DataFrame(terminology_list,columns=["term"])
    return df

def read_reference_abstract(ref_filename):
    # CNKI，搜关键词，以“被引次数”排序，全选-导出文献-知网研学-复制到剪贴板，粘贴到文本文件中。
    with open(ref_filename, 'r', encoding='utf-8') as f:
        reference_list=f.readlines()
    # 仅仅保留开头为“Title-题名”,“Keyword-关键词”,“Summary-摘要”这三个的行
    reference_filtered = [x for x in reference_list if x.startswith("Title-题名") or x.startswith("Summary-摘要")] 
    reference_filtered = [x.replace("Title-题名: ","").replace("Summary-摘要: ","").replace("\n","") for x in reference_filtered]
    reference_filtered = [x.replace("目的","").replace("方法","").replace("结果","").replace("结论","").replace(":","").replace(" ","").replace("\n","").replace("\u3000","") for x in reference_filtered]
    reference_filtered = [x.split("。") for x in reference_filtered]
    reference_filtered = [item for sublist in reference_filtered for item in sublist]
    reference_filtered = set(reference_filtered)
    reference_filtered = [x for x in reference_filtered if x != ""]
    df=pd.DataFrame(reference_filtered,columns=["term"])
    return df 

def read_reference(ref_filename):
    with open(ref_filename, 'r', encoding='utf-8') as f:
        reference_list=f.readlines()
    reference_filtered = [x.replace("Title-题名: ","").replace("Summary-摘要: ","").replace("\n","") for x in reference_list]
    reference_filtered = [x.split("。") for x in reference_filtered]
    df=pd.DataFrame(reference_filtered,columns=["term"])
    return df 

def get_embedding_from_terminology(terminology_filename,
                                embedding_model = "text-embedding-ada-002"):
    df=read_terminology(terminology_filename)
    # df=read_reference_abstract(terminology_filename)
    # print(df.head())
    if df is None:
        return None
    df["embedding"]=df["term"].apply(lambda x: get_embedding(x,embedding_model))
    return df


def build_terminology(terminology_filename,
                      embedding_model = "text-embedding-ada-002",
                      ):
    # 不管有没有都重建一次好了，估计不费事
    df=get_embedding_from_terminology(terminology_filename,embedding_model)
    return df



def terminology_prompt(text, terminology,
                    term_candidate_n=5,
                    embedding_model="text-embedding-ada-002"):
    if terminology is None:
        return ""
    
    text_embedding=get_embedding(text, engine=embedding_model)
    terminology["similarity"]=terminology["embedding"].apply(lambda x: cosine_similarity(x,text_embedding))
    results = terminology.sort_values("similarity", ascending=False, ignore_index=True)
    results = results["term"].head(term_candidate_n)
    terminology_list=", ".join(results.to_list())
    terminology_promt=f"and use the following terminology list if necessary: [{terminology_list}], but please do NOT show the terminology directly in the results. "
    return terminology_promt
