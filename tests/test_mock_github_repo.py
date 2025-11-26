"""
Tests for MockedGithubRepoRetriever.

Verifies that the mock repo retriever can:
- Store files via add_file()
- Generate deterministic markdown exports
- Include repo header, file lists, and content
"""
import pytest
from integration_coworker.repo.mock_github import MockedGithubRepoRetriever


def test_empty_repo():
    """Test that an empty repo produces valid markdown."""
    retriever = MockedGithubRepoRetriever("test-repo", "test-owner")
    
    markdown = retriever.export_markdown()
    
    assert "# Repository: test-owner/test-repo" in markdown
    assert "Total files: 0" in markdown


def test_add_single_file():
    """Test adding a single file and exporting markdown."""
    retriever = MockedGithubRepoRetriever("my-repo")
    
    retriever.add_file("src/main.py", "print('hello')")
    
    markdown = retriever.export_markdown()
    
    # Check header
    assert "# Repository: mocked-user/my-repo" in markdown
    
    # Check file is listed
    assert "src/main.py" in markdown
    
    # Check content appears
    assert "print('hello')" in markdown
    assert "```python" in markdown


def test_add_multiple_files():
    """Test adding multiple files of different types."""
    retriever = MockedGithubRepoRetriever("multi-file-repo", "acme-corp")
    
    retriever.add_file("README.md", "# Project\n\nDescription here")
    retriever.add_file("src/app.py", "def main():\n    pass")
    retriever.add_file("tests/test_app.py", "def test_main():\n    assert True")
    retriever.add_file("config.json", '{"key": "value"}')
    
    markdown = retriever.export_markdown()
    
    # Check all files are listed
    assert "README.md" in markdown
    assert "src/app.py" in markdown
    assert "tests/test_app.py" in markdown
    assert "config.json" in markdown
    
    # Check stats
    assert "Total files: 4" in markdown
    
    # Check extension stats
    assert ".py" in markdown
    assert ".md" in markdown
    assert ".json" in markdown


def test_file_replacement():
    """Test that add_file replaces existing files."""
    retriever = MockedGithubRepoRetriever("replace-test")
    
    retriever.add_file("file.txt", "original content")
    retriever.add_file("file.txt", "updated content")
    
    markdown = retriever.export_markdown()
    
    assert "updated content" in markdown
    assert "original content" not in markdown
    assert "Total files: 1" in markdown


def test_calculate_stats():
    """Test that stats calculation works correctly."""
    retriever = MockedGithubRepoRetriever("stats-test")
    
    retriever.add_file("a.py", "x" * 100)
    retriever.add_file("b.py", "y" * 200)
    retriever.add_file("c.txt", "z" * 50)
    
    stats = retriever.calculate_stats()
    
    assert stats["total_files"] == 3
    assert stats["total_size"] == 350
    assert stats["extensions"][".py"] == 2
    assert stats["extensions"][".txt"] == 1


def test_generate_tree():
    """Test directory tree generation."""
    retriever = MockedGithubRepoRetriever("tree-test")
    
    retriever.add_file("README.md", "content")
    retriever.add_file("src/main.py", "code")
    retriever.add_file("src/utils/helper.py", "helper")
    
    tree = retriever.generate_tree()
    
    assert "tree-test/" in tree
    assert "README.md" in tree
    assert "main.py" in tree
    assert "helper.py" in tree


def test_long_file_truncation():
    """Test that very long files are truncated in markdown export."""
    retriever = MockedGithubRepoRetriever("truncation-test")
    
    # Create a file with 150 lines
    long_content = "\n".join([f"line {i}" for i in range(150)])
    retriever.add_file("long.txt", long_content)
    
    markdown = retriever.export_markdown()
    
    # Should mention truncation
    assert "more lines omitted" in markdown or "omitted" in markdown.lower()


def test_deterministic_output():
    """Test that export_markdown produces deterministic output."""
    retriever1 = MockedGithubRepoRetriever("deterministic", "owner")
    retriever2 = MockedGithubRepoRetriever("deterministic", "owner")
    
    files = [
        ("z.py", "last"),
        ("a.py", "first"),
        ("m.py", "middle"),
    ]
    
    # Add in different orders
    for path, content in files:
        retriever1.add_file(path, content)
    
    for path, content in reversed(files):
        retriever2.add_file(path, content)
    
    # Should produce identical output (sorted)
    assert retriever1.export_markdown() == retriever2.export_markdown()
