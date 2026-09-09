"""Import and controller-shape contracts for Frappe's generated loaders."""

from frappe.model.document import Document

from karam_finance.karam_series.doctype.doctype_list.doctype_list import DoctypeList
from karam_finance.karam_series.doctype.karam_series.karam_series import KaramSeries


def test_generated_doctype_controllers_are_loadable_document_subclasses() -> None:
    assert issubclass(DoctypeList, Document)
    assert issubclass(KaramSeries, Document)
