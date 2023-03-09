import pandas as pd
from openai.embeddings_utils import get_embedding
import os

def read_glossary(glossary_path):
    glossary_list=[]
    with os.scandir(glossary_path) as it:
        for entry in it:
            if entry.name.endswith(".txt") or entry.name.endswith(".csv"):
                with open(entry.path, 'r', encoding='utf-8') as f:
                    for line in f:
                        glossary_list.append(line.strip())
    df=pd.DataFrame(glossary_list,columns=["term"])
    return df

def get_embedding_from_glossary(glossary_path,
                                embedding_model = "text-embedding-ada-002"):
    glossary_df=read_glossary(glossary_path)
    glossary_df["embedding"]=glossary_df["term"].apply(lambda x: get_embedding(x,embedding_model))
    return glossary_df

if __name__=="__main__":
    glossary_path="../../glossary"
    glossary_filaname="glossary.pkl"
    get_embedding_from_glossary(glossary_path).to_pickle(os.path.join(glossary_path,glossary_filaname))