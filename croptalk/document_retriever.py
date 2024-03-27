import os
from io import BytesIO
from typing import List, Optional

import PyPDF2
import boto3
from dotenv import load_dotenv
from weaviate.collections.classes.internal import QueryReturn

from croptalk.weaviate_utils import (
    get_client_collection,
    query_near_vector_with_filters,
)

load_dotenv("secrets/.env.secret")
load_dotenv("secrets/.env.shared")


class DocumentRetriever:
    """
    Class responsible for document retrieval in a weaviate vector store.
    """

    def __init__(self, collection_name: str) -> None:
        """
        Connecting to weaviate cloud services requires the following environment variables to be
        set:
            - WCS_CLUSTER_URL
            - WCS_API_KEY

        Args:
            collection_name: Name of weaviate collection
        """
        self.collection_name = collection_name
        self.collection = get_client_collection(self.collection_name)[1]

    def get_documents(
            self,
            query: str,
            doc_category: Optional[str] = None,
            commodity: Optional[str] = None,
            county: Optional[str] = None,
            state: Optional[str] = None,
            top_k: int = 3,
            include_common_docs: bool = True,
    ) -> List[str]:
        """
        Args:
            query: query to use for document retrieval
            doc_category: document category to filter on, None means no filter
            commodity: commodity to filter on, None means no filter
            county: county to filter on, None means no filter
            state: state to filter on, None means no filter
            top_k: number of retrieved documents we are aiming for, defaults to 3
            include_common_docs: whether (default) or not to include documents that apply to all
                                 states, counties or commodities... does not apply to document
                                 category filter

        Returns:
            list of retrieved documents matching query, filters and top_k
        """
        if not isinstance(query, str):
            raise ValueError(f"Query must be a string. Received: {query}")

        # query vector store
        query_response = query_near_vector_with_filters(
            collection=self.collection,
            query=query,
            limit=top_k,
            doc_category=doc_category,
            commodity=commodity,
            county=county,
            state=state,
            include_common_docs=include_common_docs,
        )

        # format returned docs
        formatted_docs = self._format_query_response(query_response)
        return formatted_docs

    @staticmethod
    def _read_pdf_from_s3(bucket_name, file_key):
        # Initialize S3 client
        s3 = boto3.client('s3',
                          aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                          aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])

        # Download PDF file from S3
        response = s3.get_object(Bucket=bucket_name, Key=file_key)
        pdf_data = response['Body'].read()

        # Read text content from the PDF
        pdf_reader = PyPDF2.PdfReader(BytesIO(pdf_data))
        text_content = ''
        for page_num in range(len(pdf_reader.pages)):
            text_content += pdf_reader.pages[page_num].extract_text()

        return text_content

    @staticmethod
    def _clean_sp_content(content: str) -> str:
        """
        Removing multiple spaces and line skips to save on tokens
        """
        return ' '.join(content.replace("\n", " ").split())

    @staticmethod
    def _find_duplicates_indices(enum):
        seen = {}
        duplicates = []

        for idx, item in enum:
            if item in seen:
                duplicates.append(idx)
            else:
                seen[item] = idx

        return duplicates

    def _get_index_from_unique_sp_s3_keys(self, query_response: QueryReturn) -> List[int]:
        # getting list of duplicated s3 keys for doc which are SP
        s3_keys = [(i, doc) for i, doc in enumerate(query_response.objects) if doc.properties["doc_category"] == "SP"]
        duplicated_sp_idx = self._find_duplicates_indices(s3_keys)

        # get all unique indexes without SP duplicates
        return [i for i, j in enumerate(query_response.objects) if i not in duplicated_sp_idx]

    def _get_content(self, doc_property) -> str:
        """
        If the document is of type SP, we extract the full text of the document to be inserted in the prompt
        Args:
            doc_property: properties attribute from query response objects
        Returns: formatted str
        """
        print("CLEANING CONTENT")
        if doc_property["doc_category"] == "SP":
            content = self._read_pdf_from_s3("croptalk-spoi", doc_property['s3_key'])
            content = self._clean_sp_content(content)
        else:
            content = doc_property['content']
        print(content)
        return f">{content}</doc>"

    def _format_query_response(self, query_response: QueryReturn) -> List[str]:
        """
        Args:
            query_response: weaviate query response

        Returns:
            list of retrieved and formatted documents, equivalent to provided query response
        """

        unique_index = self._get_index_from_unique_sp_s3_keys(query_response)

        print("UNIQUE INDEX", unique_index)

        response_objects = [
            f"<doc"
            f" id='{i + 1}'"
            f" title='{doc.properties['title']}'"
            f" page_id='{doc.properties['page']}'"
            f" doc_category='{doc.properties['doc_category']}'"
            f" commodity='{doc.properties['commodity']}'"
            f" state='{doc.properties['state']}'"
            f" county='{doc.properties['county']}'"
            f" s3_key='{doc.properties['s3_key']}'"
            f" url='https://croptalk-spoi.s3.us-east-2.amazonaws.com/{doc.properties['s3_key']}'"
            + self._get_content(doc.properties)
            for i, doc in enumerate(query_response.objects) if i in unique_index
        ]

        print("RESPONSE OBJECT", response_objects)

        return response_objects
