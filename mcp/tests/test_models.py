from notion_mcp.models import (
    make_paragraph_block,
    make_heading_block,
    make_todo_block,
    make_divider_block,
)

def test_make_paragraph_block():
    block = make_paragraph_block("Hello world")
    assert block["object"] == "block"
    assert block["type"] == "paragraph"
    assert block["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"

def test_make_heading_block():
    block = make_heading_block("Title", level=2)
    assert block["type"] == "heading_2"
    assert "heading_2" in block

def test_make_todo_block():
    block = make_todo_block("Task", checked=True)
    assert block["type"] == "to_do"
    assert block["to_do"]["checked"] is True

def test_make_divider_block():
    block = make_divider_block()
    assert block["type"] == "divider"
