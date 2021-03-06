# Copyright 2016 Google Inc. All Rights Reserved.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Module for utility functions that manipulate or compare data.

We use a few special data formats internally, these utility functions make it a
little easier to work with them.
"""

import collections
import difflib
import itertools
import logging
import numbers
import pprint
import struct
import sys

from mutablerecords import records

from enum import Enum


def pprint_diff(first, second, first_name='first', second_name='second'):
  """Compare the pprint representation of two objects and yield diff lines."""
  return difflib.unified_diff(
      pprint.pformat(first).splitlines(),
      pprint.pformat(second).splitlines(),
      fromfile=first_name, tofile=second_name, lineterm='')


def equals_log_diff(expected, actual, level=logging.ERROR):
  """Compare two string blobs, error log diff if they don't match."""
  if expected == actual:
    return True

  # Output the diff first.
  logging.log(level, '***** Data mismatch: *****')
  for line in difflib.unified_diff(
      expected.splitlines(), actual.splitlines(),
      fromfile='expected', tofile='actual', lineterm=''):
    logging.log(level, line)
  logging.log(level, '^^^^^  Data diff  ^^^^^')


def assert_records_equal_nonvolatile(first, second, volatile_fields, indent=0):
  """Compare two test_record tuples, ignoring any volatile fields.

  'Volatile' fields include any fields that are expected to differ between
  successive runs of the same test, mainly timestamps.  All other fields
  are recursively compared.
  """
  if isinstance(first, dict) and isinstance(second, dict):
    if set(first) != set(second):
      logging.error('%sMismatching keys:', ' ' * indent)
      logging.error('%s  %s', ' ' * indent, first.keys())
      logging.error('%s  %s', ' ' * indent, second.keys())
      assert set(first) == set(second)
    for key in first:
      if key in volatile_fields:
        continue
      try:
        assert_records_equal_nonvolatile(first[key], second[key],
                                         volatile_fields, indent + 2)
      except AssertionError:
        logging.error('%sKey: %s ^', ' ' * indent, key)
        raise
  elif hasattr(first, '_asdict') and hasattr(second, '_asdict'):
    # Compare namedtuples as dicts so we get more useful output.
    assert_records_equal_nonvolatile(first._asdict(), second._asdict(),
                                     volatile_fields, indent)
  elif hasattr(first, '__iter__') and hasattr(second, '__iter__'):
    for idx, (fir, sec) in enumerate(itertools.izip(first, second)):
      try:
        assert_records_equal_nonvolatile(fir, sec, volatile_fields, indent + 2)
      except AssertionError:
        logging.error('%sIndex: %s ^', ' ' * indent, idx)
        raise
  elif (isinstance(first, records.RecordClass) and
        isinstance(second, records.RecordClass)):
    assert_records_equal_nonvolatile(
        {slot: getattr(first, slot) for slot in first.__slots__},
        {slot: getattr(second, slot) for slot in second.__slots__},
        volatile_fields, indent)
  elif first != second:
    logging.error('%sRaw: "%s" != "%s"', ' ' * indent, first, second)
    assert first == second


def convert_to_base_types(obj, ignore_keys=tuple(), tuple_type=tuple):
  """Recursively convert objects into base types, mostly dicts and strings.

  This is used to convert some special types of objects used internally into
  base types for more friendly output via mechanisms such as JSON.  It is used
  for sending internal objects via the network and outputting test records.
  Specifically, the conversions that are performed:

    - If an object has an _asdict() method, use that to convert it to a dict.
    - mutablerecords Record instances are converted to dicts that map
      attribute name to value.  Optional attributes with a value of None are
      skipped.
    - Enum instances are converted to strings via their .name attribute.
    - Number types are left as such (instances of numbers.Number).
    - Byte and unicode strings are left alone (instances of basestring).
    - Other non-None values are converted to strings via str().

  This results in the return value containing only dicts, lists, tuples,
  strings, Numbers, and None.  If tuples should be converted to lists (ie
  for an encoding that does not differentiate between the two), pass
  'tuple_type=list' as an argument.
  """
  # Because it's *really* annoying to pass a single string accidentally.
  assert not isinstance(ignore_keys, basestring), 'Pass a real iterable!'

  if hasattr(obj, '_asdict'):
    obj = obj._asdict()
  elif isinstance(obj, records.RecordClass):
    obj = {attr: getattr(obj, attr)
           for attr in type(obj).all_attribute_names
           if (getattr(obj, attr) is not None or
               attr in type(obj).required_attributes)}
  elif isinstance(obj, Enum):
    obj = obj.name

  # Recursively convert values in dicts, lists, and tuples.
  if isinstance(obj, dict):
    obj = {convert_to_base_types(k, ignore_keys, tuple_type):
               convert_to_base_types(v, ignore_keys, tuple_type)
           for k, v in obj.iteritems() if k not in ignore_keys}
  elif isinstance(obj, list):
    obj = [convert_to_base_types(val, ignore_keys, tuple_type) for val in obj]
  elif isinstance(obj, tuple):
    obj = tuple_type(
        convert_to_base_types(value, ignore_keys, tuple_type) for value in obj)
  elif obj is not None and (
      not isinstance(obj, numbers.Number) and not isinstance(obj, basestring)):
    # Leave None as None to distinguish it from "None", as well as numbers and
    # strings, converting anything unknown to strings.
    obj = str(obj)

  return obj


def total_size(obj):
  """Returns the approximate total memory footprint an object."""
  seen = set()
  def sizeof(current_obj):
    """Do a depth-first acyclic traversal of all reachable objects."""
    if id(current_obj) in seen:
      # A rough approximation of the size cost of an additional reference.
      return struct.calcsize('P')
    seen.add(id(current_obj))
    size = sys.getsizeof(current_obj)

    if isinstance(current_obj, dict):
      size += sum(map(sizeof, itertools.chain.from_iterable(
          current_obj.iteritems())))
    elif (isinstance(current_obj, collections.Iterable) and
          not isinstance(current_obj, basestring)):
      size += sum(sizeof(item) for item in current_obj)
    elif isinstance(current_obj, records.RecordClass):
      size += sum(sizeof(getattr(current_obj, attr))
                  for attr in current_obj.__slots__)
    return size

  return sizeof(obj)
