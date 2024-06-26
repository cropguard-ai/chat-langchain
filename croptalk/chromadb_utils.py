from typing import Callable, Dict

from dsmain.dataapi.lookups import CommodityLookup, StateLookup, CountyLookup, InsurancePlanLookup

state_lookup = StateLookup(quiet_fail=True)
county_lookup = CountyLookup(quiet_fail=True)
commodity_lookup = CommodityLookup(quiet_fail=True)


def create_chroma_filter(state: str = None, county: str = None, commodity: str = None, doc_category: str = None,
                         include_common_docs: bool = True) -> Dict:
    """Creates a where_filter for chromadb retriever based on the provided arguments.
    See syntax documentation here: https://docs.trychroma.com/usage-guide#using-where-filters
    """

    def is_valid_param(param):
        return param and param.lower() != 'none'

    def add_filter_condition(field, code):
        if include_common_docs:
            common_code = '00' if field != 'commodity' else '0000'
            condition = {"$in": [code, common_code]}
        else:
            condition = {"$eq": code}

        where_filter["$and"].append({field: condition})

    where_filter = {"$and": []}

    if is_valid_param(state):
        state_obj = state_lookup.find(state)
        if state_obj:
            add_filter_condition("state", state_obj.code)

    if is_valid_param(county) and state:
        county_obj = county_lookup.find_by_name(county, state)
        if county_obj:
            add_filter_condition("county", county_obj.code)

    if is_valid_param(commodity):
        commodity_obj = commodity_lookup.find(commodity)
        if commodity_obj:
            add_filter_condition("commodity", commodity_obj.code)

    if doc_category is not None:
        where_filter["$and"].append({"doc_category": {"$eq": doc_category}})

    if len(where_filter["$and"]) == 0:
        return {}
    elif len(where_filter["$and"]) == 1:
        return where_filter["$and"][0]

    return where_filter
