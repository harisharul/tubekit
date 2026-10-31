"""Offline tests for transcript search; no network access needed."""

import pytest

from tubekit_mcp.youtube import YouTubeError, keyword_pattern, search_segments, video_id


def segs(*items):
    return [{"start": float(t), "text": text} for t, text in items]


TRANSCRIPT = segs(
    (0, "welcome to this neural network tutorial"),
    (10, "today we cover the glossary of terms"),
    (100, "to train we need a loss"),
    (103, "function that measures how wrong we are"),
    (110, "for classification use cross-entropy"),
    (300, "the Loss Functions we saw earlier"),
    (600, "mean squared error is another option"),
)


def test_phrase_split_across_caption_lines_is_found():
    result = search_segments(TRANSCRIPT, ["loss function"])
    assert result["keyword_counts"]["loss function"] == 2
    assert [r["start"] for r in result["ranges"]] == [100, 300]


def test_word_start_matching_ignores_substrings():
    assert search_segments(TRANSCRIPT, ["loss"])["keyword_counts"]["loss"] == 2  # not "glossary"


def test_hyphen_and_space_are_interchangeable():
    assert search_segments(TRANSCRIPT, ["cross entropy"])["keyword_counts"]["cross entropy"] == 1


def test_nearby_hits_merge_into_one_range():
    result = search_segments(TRANSCRIPT, ["loss function", "cross-entropy"])
    first = result["ranges"][0]
    assert (first["start"], first["end"]) == (100, 300)
    assert first["counts"] == {"loss function": 1, "cross-entropy": 1}


def test_ranges_ranked_by_distinct_keywords_then_returned_in_time_order():
    result = search_segments(TRANSCRIPT, ["loss function", "cross-entropy", "mean squared"], max_results=2)
    assert result["total_ranges"] == 3
    assert result["top_range"]["start"] == 100
    assert [r["start"] for r in result["ranges"]] == [100, 300]


def test_zero_hit_keywords_are_reported():
    assert search_segments(TRANSCRIPT, ["backpropagation"])["keyword_counts"] == {"backpropagation": 0}


def test_chapter_titles_are_matched():
    chapters = [{"start": 90, "title": "Training & Loss"}, {"start": 500, "title": "Wrap-up"}]
    result = search_segments(TRANSCRIPT, ["loss"], chapters=chapters)
    assert [c["title"] for c in result["chapter_matches"]] == ["Training & Loss"]


def test_last_range_ends_at_video_duration():
    result = search_segments(TRANSCRIPT, ["squared"], duration=620)
    assert result["ranges"][0]["end"] == 620


def test_snippet_quotes_the_match():
    snippet = search_segments(TRANSCRIPT, ["cross-entropy"])["ranges"][0]["snippet"]
    assert "cross-entropy" in snippet


def test_symbol_keywords_match_literally():
    pattern = keyword_pattern("C++")
    assert pattern.search("written in C++ today")
    assert not pattern.search("written in C today")


def test_indic_scripts_match_whole_words():
    # Tamil vowel signs are combining marks; the match must not start mid-word.
    pattern = keyword_pattern("கணினி")
    assert pattern.search("இது கணினி அறிவியல்")
    assert not pattern.search("மைகணினி")


def test_duplicate_and_blank_keywords_are_dropped():
    assert list(search_segments(TRANSCRIPT, ["loss", "loss", " "])["keyword_counts"]) == ["loss"]
    with pytest.raises(YouTubeError):
        search_segments(TRANSCRIPT, ["  "])


@pytest.mark.parametrize(
    "value",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
        "https://youtu.be/dQw4w9WgXcQ",
        "youtube.com/shorts/dQw4w9WgXcQ",
    ],
)
def test_video_id_forms(value):
    assert video_id(value) == "dQw4w9WgXcQ"


def test_equivalent_keywords_are_not_double_counted():
    result = search_segments(TRANSCRIPT, ["cross-entropy", "Cross Entropy", "cross entropy"])
    assert result["keyword_counts"] == {"cross-entropy": 1}
