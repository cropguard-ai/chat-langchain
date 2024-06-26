import logging
import math
from typing import List, NamedTuple, Union

import json
from langchain_core.tracers.context import tracing_v2_enabled
from langchain_core.tracers.langchain import LangChainTracer
from langchain_core.tracers.schemas import Run
from langchain.schema.runnable import Runnable
from _scripts.utils import get_output_path, get_nodes, parse_args
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


def ingest_use_cases(eval_path: str) -> pd.DataFrame:
    """
    Args:
        eval_path: path of csv file that contains use cases

    Returns:
        dataframe that contains use cases to run
    """
    # read use cases
    eval_df = pd.read_csv(args.eval_path, header=0, dtype=str)

    # add expanded expected page number columns
    def _expand_pages(pages_in: Union[str, float]) -> List[int]:
        """ accepted input: NaN, '3' or '4,8-11' """
        pages_out = []
        if isinstance(pages_in, float) and math.isnan(pages_in):
            return pages_out
        for v in str(pages_in).split(","):
            v = v.strip()
            if v.isnumeric():
                # append single page number
                pages_out.append(int(v))
            else:
                # append range of page numbers
                lower, upper = v.split("-")
                pages_out.extend(
                    list(
                        range(
                            int(lower),
                            int(upper) + 1,
                        )
                    )
                )
        return pages_out

    def _add_page_expanded_col(expected_doc_id: int) -> None:
        expected_page_col = f"retrieved_doc{expected_doc_id}_page_expected"
        expected_page_expanded_col = f"retrieved_doc{expected_doc_id}_page_expected_expanded"
        eval_df[expected_page_expanded_col] = eval_df[expected_page_col].apply(_expand_pages)

    _add_page_expanded_col(1)
    _add_page_expanded_col(2)
    _add_page_expanded_col(3)

    return eval_df


def find_last_finddocs_node(
    langchain_tracer: LangChainTracer, output_df: pd.DataFrame, eval_use_case: NamedTuple,
) -> Run:
    # retrieve all FindDocs nodes
    finddocs_nodes = get_nodes(
        root_node=langchain_tracer.latest_run,
        node_name="FindDocs",
    )
    output_df.loc[output_df.index == eval_use_case.Index, "nb_of_FindDocs_nodes_actual"] = len(finddocs_nodes)
    # return last FindDocs node
    if len(finddocs_nodes) == 0:
        logger.info("No FindDocs node found... *_actual columns will not be filled")
        last_node = None
    if len(finddocs_nodes) == 1:
        last_node = finddocs_nodes[0]
    else:
        logger.info(f"Found {len(finddocs_nodes)} FindDocs node(s)... picking the last one to fill *_actual columns")
        max_datetime = max(n.start_time for n in finddocs_nodes)
        last_node = next(n for n in finddocs_nodes if n.start_time == max_datetime)
    return last_node


def run_use_case(model: Runnable, eval_use_case: NamedTuple, output_df: pd.DataFrame) -> None:
        logger.info(f"Running model on use case {eval_use_case}")

        # run model on use case query
        with tracing_v2_enabled() as langchain_tracer:
            model.invoke({
                "chat_history": [],
                "question": eval_use_case.query,
            })

        # retrieve last FindDocs node
        last_node = find_last_finddocs_node(langchain_tracer, output_df, eval_use_case)

        if last_node:
            # extract inputs from last FindDocs node
            inputs_dict = (
                last_node.inputs if args.use_model_llm
                else json.loads(last_node.inputs["input"].replace("'", '"'))
            )
            inputs_dict = {
                k: None if isinstance(v, str) and v.lower() == 'none' else v
                for k, v in inputs_dict.items()
            }
            # extract outputs from last FindDocs node
            outputs_list = last_node.outputs["output"]
            if not args.use_model_llm:
                # make string valid for json to load as list of strings/documents
                outputs_list = outputs_list.replace('"', "'")
                outputs_list = outputs_list.replace("'<doc ", '"<doc ')
                outputs_list = outputs_list.replace("</doc>'", '</doc>"')
                outputs_list = outputs_list.replace("\\'", "'")
                outputs_list = json.loads(outputs_list)
            # fill *_actual columns in output_df with last FindDocs node
            if "state" in inputs_dict:
                output_df.loc[output_df.index == eval_use_case.Index, "state_filter_actual"] = inputs_dict["state"]
            if "county" in inputs_dict:
                output_df.loc[output_df.index == eval_use_case.Index, "county_filter_actual"] = inputs_dict["county"]
            if "commodity" in inputs_dict:
                output_df.loc[output_df.index == eval_use_case.Index, "commodity_filter_actual"] = inputs_dict["commodity"]
            if "doc_category" in inputs_dict:
                output_df.loc[output_df.index == eval_use_case.Index, "doc_category_filter_actual"] = inputs_dict["doc_category"]
            if len(outputs_list) >= 1:
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc1_actual"] = extract_s3_key(outputs_list[0])
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc1_page_actual"] = extract_page_id(outputs_list[0])
            if len(outputs_list) >= 2:
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc2_actual"] = extract_s3_key(outputs_list[1])
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc2_page_actual"] = extract_page_id(outputs_list[1])
            if len(outputs_list) >= 3:
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc3_actual"] = extract_s3_key(outputs_list[2])
                output_df.loc[output_df.index == eval_use_case.Index, "retrieved_doc3_page_actual"] = extract_page_id(outputs_list[2])


def evaluate_use_case(output_df: pd.DataFrame) -> None:
    # evaluate use case, filter-wise
    def _get_filter_match(col_filter_actual: str, col_filter_expected: str) -> pd.Series:
        # case insensitive check between each actual and expected filter
        mask_both_na = (
            output_df[col_filter_actual].isna()
            & output_df[col_filter_expected].isna()
        )
        mask_both_equal = (
            output_df[col_filter_actual].str.lower() == output_df[col_filter_expected].str.lower()
        )
        return mask_both_na | mask_both_equal

    output_df["state_filter_match"] = _get_filter_match("state_filter_actual", "state_filter_expected")
    output_df["county_filter_match"] = _get_filter_match("county_filter_actual", "county_filter_expected")
    output_df["commodity_filter_match"] = _get_filter_match("commodity_filter_actual", "commodity_filter_expected")
    output_df["doc_category_filter_match"] = _get_filter_match("doc_category_filter_actual", "doc_category_filter_expected")

    # evaluate use case, retrieved documents-wise
    def _get_expected_doc_match(retrieved_doc_expected_col: str) -> pd.Series:
        # check that provided expected retrieved document is either
        # - NA
        # or
        # - equal to any of the actual retrieved documents (case sensitive)
        return (
            (output_df[retrieved_doc_expected_col].isna())
            |
            (output_df[retrieved_doc_expected_col] == output_df["retrieved_doc1_actual"])
            |
            (output_df[retrieved_doc_expected_col] == output_df["retrieved_doc2_actual"])
            |
            (output_df[retrieved_doc_expected_col] == output_df["retrieved_doc3_actual"])
        )

    output_df["retrieved_doc1_match"] = _get_expected_doc_match("retrieved_doc1_expected")
    output_df["retrieved_doc2_match"] = _get_expected_doc_match("retrieved_doc2_expected")
    output_df["retrieved_doc3_match"] = _get_expected_doc_match("retrieved_doc3_expected")

    # evaluate use case, retrieved document pages-wise
    def _get_page_match(expected_doc_id: int, actual_doc_id: int) -> pd.Series:
        expected_doc_col = f"retrieved_doc{expected_doc_id}_expected"
        expected_page_expanded_col = f"retrieved_doc{expected_doc_id}_page_expected_expanded"
        actual_doc_col = f"retrieved_doc{actual_doc_id}_actual"
        actual_page_col = f"retrieved_doc{actual_doc_id}_page_actual"

        no_expected_doc_mask = output_df[expected_doc_col].isna()
        matching_doc_mask = (
            output_df[actual_doc_col] == output_df[expected_doc_col]
        )
        matching_page_mask = (
            output_df.apply(
                lambda row: (
                    not row[expected_page_expanded_col]
                    or (
                        row[actual_page_col] is not None
                        and
                        int(row[actual_page_col]) in row[expected_page_expanded_col]
                    )
                ),
                axis=1,
            )
        )

        return (
            no_expected_doc_mask
            |
            (
                matching_doc_mask & matching_page_mask
            )
        )

    def _get_expected_page_match(expected_doc_id: int) -> pd.Series:
        doc_match_col = f"retrieved_doc{expected_doc_id}_match"
        return (
            output_df[doc_match_col]
            & (
                (_get_page_match(expected_doc_id, actual_doc_id=1))
                |
                (_get_page_match(expected_doc_id, actual_doc_id=2))
                |
                (_get_page_match(expected_doc_id, actual_doc_id=3))
            )
        )

    output_df["retrieved_doc1_page_match"] = _get_expected_page_match(expected_doc_id=1)
    output_df["retrieved_doc2_page_match"] = _get_expected_page_match(expected_doc_id=2)
    output_df["retrieved_doc3_page_match"] = _get_expected_page_match(expected_doc_id=3)

    # compute evaluation score
    match_cols = [
        "state_filter_match",
        "county_filter_match",
        "commodity_filter_match",
        "doc_category_filter_match",
        "retrieved_doc1_match",
        "retrieved_doc1_page_match",
        "retrieved_doc2_match",
        "retrieved_doc2_page_match",
        "retrieved_doc3_match",
        "retrieved_doc3_page_match",
    ]
    output_df["eval_score"] = output_df[match_cols].mean(axis=1)


def add_summary_row(output_df: pd.DataFrame) -> None:
    # add summary row, filled with NaNs
    summary_row_index = len(output_df)
    output_df.loc[summary_row_index] = None
    # add summary for appropriate columns
    for col in output_df.columns:
        if col.endswith("_match") or col == "eval_score":
            output_df.loc[summary_row_index, col] = output_df[col].mean()


def extract_s3_key(doc: str) -> str:
    s3_key = doc.split("s3_key='")[1].split("' url='")[0]
    return s3_key


def extract_page_id(doc: str) -> str:
    return doc.split("page_id='")[1][0]


def get_output_df(eval_df: pd.DataFrame) -> pd.DataFrame:
    output_df = eval_df.copy()
    cols_to_add = [
        col.replace("_expected", "_actual")
        for col in eval_df.columns
        if col.endswith("_expected")
    ]
    cols_to_add.append("nb_of_FindDocs_nodes_actual")
    output_df[cols_to_add] = None
    return output_df


if __name__ == "__main__":
    # parse args
    args = parse_args()
    logger.info(f"Evaluating croptalk's document retrieval capacity, using config: {args}\n")

    # read eval CSV into a df
    eval_df = ingest_use_cases(args.eval_path)
    logger.info(f"Number of use cases to evaluate: {len(eval_df)}")

    # create output df
    output_df = get_output_df(eval_df)
    logger.info("Creating output_df")

    # load model
    logger.info("Loading model")
    if args.use_model_llm:
        from croptalk.model_llm import model

        memory = None
    else:
        from croptalk.model_openai_functions import model, memory

    # run model on each use case
    for eval_use_case in eval_df.itertuples(name="EvalUseCase"):
        if memory:
            memory.clear()
        run_use_case(model, eval_use_case, output_df)

    # evaluate each use case
    evaluate_use_case(output_df)

    # add summary row
    add_summary_row(output_df)

    # save output_df
    output_path = get_output_path(args.eval_path, args.use_model_llm)
    output_df.to_csv(output_path, index=False)
    logger.info(f"Evaluation report/dataframe saved here: {output_path}")
