"""
backend/pagination.py
统一分页：默认每页 20，允许前端通过 ?page_size= 覆盖（上限 2000）。
响应结构保持 PageNumberPagination 一致（count / next / previous / results）。
"""
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 2000
