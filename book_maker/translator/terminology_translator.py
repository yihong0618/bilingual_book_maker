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

def get_embedding_from_terminology(terminology_filename,
                                embedding_model = "text-embedding-ada-002"):
    df=read_terminology(terminology_filename)
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
