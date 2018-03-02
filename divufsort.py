#! /usr/bin/env python

"""
divsufsort.py

Written by Geremy Condra
Licensed under MIT/X11 license
Released 12 October 2014

This program is a thin wrapper around libdivsufsort.
"""

import os
import sys
import collections

import ctypes
import ctypes.util

from bisect import bisect_right

#==============================================================================#
#                                 Thin Calls                                   #
#==============================================================================#

_soname = ctypes.util.find_library("divsufsort")
libdivsufsort = ctypes.CDLL(_soname)


def divsufsort(text, suffix_array):
    suffix_array_p = ctypes.cast(suffix_array, ctypes.POINTER(ctypes.c_int))
    retval = libdivsufsort.divsufsort(text, suffix_array_p, len(text))
    if retval:
        raise Exception("Couldn't create suffix array")
    return suffix_array


def divbwt(text):
    input_text = ctypes.create_string_buffer(text)
    output = ctypes.create_string_buffer(len(text))
    retval = libdivsufsort.divbwt(input_text, output, 0, len(text))
    if retval < 0:
        raise Exception("Couldn't create bwt")
    return output.value, retval


def sufcheck(text, suffix_array, verbose=1):
    verbosity = ctypes.c_int(verbose)
    suffix_array_p = ctypes.pointer(suffix_array)
    retval = libdivsufsort.sufcheck(text, suffix_array_p, len(text), verbosity)
    if retval:
        raise Exception("Suffix array check failed")
    return True


def sa_search(text, pattern, suffix_array):
    suffix_array_p = ctypes.pointer(suffix_array)
    index = ctypes.c_int(0)
    index_p = ctypes.pointer(index)
    count = libdivsufsort.sa_search(text, len(text),
                                    pattern, len(pattern),
                                    suffix_array, len(text),
                                    index_p)
    for i in range(count):
        yield suffix_array[i + index.value]

#==============================================================================#
#                               Thick Wrapper                                  #
#==============================================================================#


class SuffixArray:
    """Simple class for searching over a given text using a suffix array.

    Note that this will consume something like 6n bytes of memory, where
    n is the text size.

    The underlying implementation is Yuta Mori's divsufsort.
    """

    sa = None
    text = None

    def __init__(self, text, clone=False):
        self.text = text
        self.sa = (ctypes.c_int * len(self.text))()
        if not clone:
            divsufsort(self.text, self.sa)

    def check(self, text=None):
        """Checks whether the suffix array is correct for the given text.

        Defaults to using the current text if no text is given.
        """
        if not text:
            text = self.text
        return sufcheck(text, self.sa, False)

    def search(self, pattern):
        """Searches the text for the given pattern using the suffix array.

        Returns an iterator over the indices in the text at which the pattern
        was found.
        """
        for idx in sa_search(self.text, pattern, self.sa):
            yield idx

    def merge(self, other):
        """Merges the provided suffix array with this one.

        Assumes that the current suffix array represents the left text and the
        other the right one. Returns the suffix array resulting from the merge.
        """
        return SuffixArray(self.text + other.text)


class GeneralizedSuffixArray:
    """Implements a suffix array suitable for searching multiple documents.

    Example:
            >>> documents = {'prefix': b'ba', 'suffix': b'nana'}
            >>> sa = GeneralizedSuffixArray()
            >>> for name, document in documents:
            ...     sa.add_document(name, document)
            ...
            >>> sa.generate()
            >>> sa.is_correct()
            True
            >> for document, index in sa.search(b'ana'):
            ...     print(document, ':', index)
            ...
            suffix: 1
    """

    sa = None
    text = None
    documents = None
    docs_names = None
    docs_offsets = None

    def __init__(self):
        self.text = b''
        self.documents = collections.OrderedDict()
        self.docs_names = []
        self.docs_offsets = [0]

    def check_text_defined(self):
        """Checks whether the text has been defined.

        Note that this does not check the correctness of the document mapping.
        """
        if not self.text:
            raise ValueError("No text found- have you called add_document?")
        return True

    def check_suffix_array_defined(self):
        """Checks whether the suffix array has been initialized."""
        if not self.sa:
            raise ValueError(
                "No suffix array found- have you called generate?")
        return True

    def add_document(self, name, document):
        """Adds a new document with the given name to the suffix array.

        Name can be an arbitrary object. The document itself must be bytes or
        byte-equivalent (in python3, that means bytes).

        Note that this does not regenerate the suffix array.
        """
        self.documents[name] = len(document)
        self.text += document
        self.docs_names.append(name)
        self.docs_offsets.append(self.docs_offsets[-1] + len(document))

    def get_document_index(self, text_index):
        """Translates between a given text index and a document index.

        Returns a pair consisting of the document name and the offset into that
        document represented by the given text index.

        If the index is not found, raises IndexError.
        If the text is not defined, raises ValueError.
        """
        self.check_text_defined()
        ind = bisect_right(self.docs_offsets, text_index) - 1
        current_offset = self.docs_offsets[ind]
        name = self.docs_names[ind]
        length = self.documents[name]
        document_end = current_offset + length
        assert current_offset <= text_index < document_end
        document_offset = text_index - current_offset
        return name, document_offset

    def generate(self):
        """Generates the suffix array based on the current texts.

        Raises ValueError if no text has been defined.
        """
        self.check_text_defined()
        self.sa = (ctypes.c_int * len(self.text))()
        divsufsort(self.text, self.sa)

    def is_correct(self, text=None):
        """Checks whether the suffix array is correct for the given text.

        Defaults to using the current text if no text is given.
        """
        if not text:
            self.check_text_defined()
            text = self.text
        self.check_suffix_array_defined()
        return sufcheck(text, self.sa, False)

    def search(self, pattern):
        """Searches the text for the given pattern using the suffix array.

        Returns an iterator over (name, index) pairs indicating the index in
        each text at which a match was found.

        Note that by default this filters for so-called 'synthetic' matches, in
        which the pattern is matched across multiple texts.

        Also note that searches will return in a deterministic order, but that
        this order is probably not the most intuitive or useful one. If you
        need them in order, sort them.

        Raises ValueError if either the text or the suffix array is not yet
        defined.
        """
        self.check_text_defined()
        self.check_suffix_array_defined()
        for text_index in sa_search(self.text, pattern, self.sa):
            document_name, document_offset = self.get_document_index(
                text_index)
            pattern_end = document_offset + len(pattern)
            document_length = self.documents[document_name]
            if pattern_end <= document_length:
                yield (document_name, document_offset)

    def common_prefix(self, shorter, longer):
        i = 0
        for a, b in zip(shorter, longer):
            if a != b:
                return shorter[:i]
            i += 1

    def common_substrings(self):
        self.check_suffix_array_defined()
        for text_index, next_text_index in zip(self.sa[:-1], self.sa[1:]):
            current_name, current_offset = self.get_document_index(text_index)
            next_name, next_offset = self.get_document_index(next_text_index)
            if current_name != next_name:
                current_suffix = self.documents[current_name][current_offset:]
                next_suffix = self.documents[next_name][next_offset:]
                yield self.common_prefix(current_suffix, next_suffix)


#==============================================================================#
#                                    Tests                                     #
#==============================================================================#

def test_suffix_array_basic():
    sa = SuffixArray(b"banana")
    sa.check()


def test_suffix_array_search():
    sa = SuffixArray(b"banana")
    sa.check()
    results = sorted(list(sa.search(b'ana')))
    assert(results == [1, 3])
    results = list(sa.search(b'z'))
    assert(results == [])


def test_suffix_array_merge():
    sa = SuffixArray(b"bananaban")
    sb = SuffixArray(b"anabanana")
    sc = sa.merge(sb)
    # print(list(sc.sa))
    sc.check()


def test_suffix_array_smart_merge():
    sa = SuffixArray(b"banana")
    sb = SuffixArray(b"banana")
    print("Expected: ", [5, 10, 3, 1, 7, 0, 6, 9, 11, 4, 2, 8])
    sc = sa.smart_merge3(sb)
    sc.check()


def test_suffix_array_smart_merge_variable_length():
    sa = SuffixArray(b"banana")
    sb = SuffixArray(b"bandana")
    print("Expected, ", [12, 5, 10, 3, 1, 7, 0, 6, 9, 11, 4, 2, 8])
    sc = sa.smart_merge3(sb)
    sc.check()


def test_generalized_suffix_array_basic():
    gsa = GeneralizedSuffixArray()
    gsa.add_document("fruit", b"banana")
    gsa.generate()
    gsa.is_correct()


def test_generalized_suffix_array_search_one_document():
    gsa = GeneralizedSuffixArray()
    gsa.add_document("fruit", b"banana")
    gsa.generate()
    gsa.is_correct()
    results = sorted(list(gsa.search(b"ana")))
    assert(results == [('fruit', 1), ('fruit', 3)])
    gsa.is_correct()


def test_generalized_suffix_array_search_two_documents():
    gsa = GeneralizedSuffixArray()
    gsa.add_document("fruit", b"banana")
    gsa.add_document("veggie", b"apple")  # apples are not veggies
    gsa.generate()
    gsa.is_correct()
    results = sorted(list(gsa.search(b"ana")))
    assert(results == [('fruit', 1), ('fruit', 3)])
    results = list(gsa.search(b"app"))
    assert(results == [('veggie', 0)])
    results = list(gsa.search(b"aa"))
    assert(results == [])
    gsa.is_correct()


def test_null_safety():
    gsa = GeneralizedSuffixArray()
    gsa.add_document("fruit", b"ban\0na")
    gsa.add_document("veggie", b"apple")  # apples are not veggies
    gsa.generate()
    gsa.is_correct()
    results = list(gsa.search(b"\0na"))
    assert(results == [('fruit', 3)])
    results = list(gsa.search(b"app"))
    assert(results == [('veggie', 0)])
    results = list(gsa.search(b"aa"))
    assert(results == [])
    results = list(gsa.search(b"ba"))
    assert(results == [('fruit', 0)])
    results = list(gsa.search(b"naa"))
    assert(results == [])
    gsa.is_correct()
    # print(list(gsa.common_substrings()))


def test():
    test_suffix_array_basic()
    test_suffix_array_search()
    test_suffix_array_merge()
    # test_suffix_array_smart_merge()
    # test_suffix_array_smart_merge_variable_length()
    test_generalized_suffix_array_basic()
    test_generalized_suffix_array_search_one_document()
    test_generalized_suffix_array_search_two_documents()
    test_null_safety()


if __name__ == "__main__":
    test()
