from itertools import product
from collections import defaultdict

from AbstractRepairer import AbstractRepairer

class Repairer(AbstractRepairer):
  def repair(self, data_to_repair):
    col_ids = range(len(data_to_repair[0]))

    # Get column type information
    col_types = ["Y"]*len(col_ids)
    for i, col in enumerate(col_ids):
      if i in self.features_to_ignore:
        col_types[i] = "I"
      elif i == self.feature_to_repair:
        col_types[i] = "X"
      else:
        continue

    col_type_dict = {col_id: col_type for col_id, col_type in zip(col_ids, col_types)}
    Y_col_ids = filter(lambda x: col_type_dict[x] == "Y", col_ids)
    not_I_col_ids = filter(lambda x: col_type_dict[x] != "I", col_ids)

    # To prevent potential perils with user-provided column names, map them to safe column names
    safe_stratify_cols = [self.feature_to_repair]

    # Extract column values for each attribute in data
    # Begin by initializing keys and values in dictionary
    data_dict = {col_id: [] for col_id in col_ids}
    # Populate each attribute with its column values
    for row in data_to_repair:
      for i in col_ids:
        if i in Y_col_ids:
          data_dict[i].append(float(row[i]))
        else:
          data_dict[i].append(row[i])


    # Create unique value structures:
    # When performing repairs, we choose median values. If repair is partial, then values will
    # be modified to some intermediate value between the original and the median value. However,
    # the partially repaired value will only be chosen out of values that exist in the data set.
    # This prevents choosing values that might not make any sense in the data's context.
    # To do this, for each column, we need to sort all unique values and create two data structures:
    # a list of values, and a dict mapping values to their positions in that list. Example:
    #   There are unique_col_vals[col] = [1, 2, 5, 7, 10, 14, 20] in the column. A value 2 must be
    #   repaired to 14, but the user requests that data only be repaired by 50%. We do this by
    #   finding the value at the right index:
    #   index_lookup[col][2] = 1; index_lookup[col][14] = 5; this tells us that
    #   unique_col_vals[col][3] = 7 is 50% of the way from 2 to 14.
    unique_col_vals = {}
    index_lookup = {}
    for col_id in not_I_col_ids:
      col_values = data_dict[col_id] #TODO: Make this use all_data
      # extract unique values from column and sort
      col_values = sorted(list(set(col_values)))
      unique_col_vals[col_id] = col_values
      # look up a value, get its position
      index_lookup[col_id] = {col_values[i]: i for i in range(len(col_values))}


    # Make a list of unique values per each stratified column.
    # Then make a list of combinations of stratified groups. Example: race and gender cols are stratified:
    # [(white, female), (white, male), (black, female), (black, male)]
    # The combinations are tuples because they can be hashed and used as dictionary keys.
    # From these, find the sizes of these groups.
    unique_stratify_values = [unique_col_vals[i] for i in safe_stratify_cols]
    all_stratified_groups = list(product(*unique_stratify_values))
    # look up a stratified group, and get a list of indices corresponding to that group in the data
    stratified_group_indices = defaultdict(list)
    # Find the sizes of each combination of stratified groups in the data
    sizes = {group: 0 for group in all_stratified_groups}
    for i in range(len(data_dict[safe_stratify_cols[0]])):
      group = tuple(data_dict[col][i] for col in safe_stratify_cols)
      stratified_group_indices[group].append(i)
      sizes[group] += 1

    # Don't consider groups not present in data (size 0)
    all_stratified_groups = filter(lambda x: sizes[x], all_stratified_groups)

    # Separate data by stratified group to perform repair on each Y column's values given that their
    # corresponding protected attribute is a particular stratified group. We need to keep track of each Y column's
    # values corresponding to each particular stratified group, as well as each value's index, so that when we
    # repair the data, we can modify the correct value in the original data. Example: Supposing there is a
    # Y column, "Score1", in which the 3rd and 5th scores, 70 and 90 respectively, belonged to black women,
    # the data structure would look like: {("Black", "Woman"): {Score1: [(70,2),(90,4)]}}
    stratified_group_data = {group: {} for group in all_stratified_groups}
    for group in all_stratified_groups:
      for col_id in data_dict:
        stratified_col_values = sorted([(data_dict[col_id][i], i) for i in stratified_group_indices[group]], key=lambda vals: vals[0])
        stratified_group_data[group][col_id] = stratified_col_values

    # Find the combination with the fewest data points. This will determine what the quantiles are.
    num_quantiles = min(filter(lambda x: x, sizes.values()))

    # Repair Data and retrieve the results

    quantile_unit = 1.0/num_quantiles
    for col_id in filter(lambda x: col_type_dict[x] == "Y", col_ids):
      # which bucket value we're repairing
      group_offsets = {group: 0 for group in all_stratified_groups}
      col = data_dict[col_id]
      for quantile in range(num_quantiles):
        values_at_quantile = []
        indices_per_group = {}
        for group in all_stratified_groups:
          offset = int(round(group_offsets[group]*sizes[group]))
          number_to_get = int(round((group_offsets[group] + quantile_unit)*sizes[group]) - offset)
          group_offsets[group] += quantile_unit

          # get data at this quantile from this Y column such that stratified X = group
          group_data_at_col = stratified_group_data[group][col_id]
          # (val, index) -> tuple
          indices_per_group[group] = [x[1] for x in group_data_at_col[offset:offset+number_to_get]]

          values =  [x[0] for x in group_data_at_col[offset:offset+number_to_get]]
        # Find this group's median value at this quantile
          values_at_quantile.append(sorted([float(x) for x in values])[len(values)/2])

        # Find the median value of all groups at this quantile (chosen from each group's medians)
        median = sorted(values_at_quantile)[len(values_at_quantile)/2]
        median_val_pos = index_lookup[col_id][median]

        # Update values to repair the dataset!
        for group in all_stratified_groups:
          for index in indices_per_group[group]:
            original_value = col[index]

            current_val_pos = index_lookup[col_id][original_value]
            distance = median_val_pos - current_val_pos # distance between indices
            distance_to_repair = int(round(distance * self.repair_level))
            index_of_repair_value = current_val_pos + distance_to_repair
            repaired_value = unique_col_vals[col_id][index_of_repair_value]

            # Update data to repaired valued
            data_dict[col_id][index] = repaired_value

    repaired_data = []
    for i, orig_row in enumerate(data_to_repair):
      new_row = [orig_row[j] if j in self.features_to_ignore else data_dict[j][i] for j in col_ids]
      repaired_data.append(new_row)

    return repaired_data



def test():
  test_minimal()
  test_ricci()

def test_minimal():
  class_1 = [[float(i),"A"] for i in xrange(0, 100)]
  class_2 = [[float(i),"B"] for i in xrange(101, 200)]
  data = class_1 + class_2

  feature_to_repair = 1
  repairer = Repairer(data, feature_to_repair, 0.5)
  repaired_data = repairer.repair(data)
  print "CategoricRepairer -- Minimal Dataset -- repaired_data altered?", repaired_data != data

def test_ricci():
  import csv
  filepath = "test_data/RicciDataMod.csv"
  ignored_features = [0, 5] # Identifier columns and response columns.
  feature_to_repair = 3
  repair_level = 0.5

  data = []
  with open(filepath) as f:
    for row in csv.reader(f):
      data.append(row)

  data.pop(0)

  repairer = Repairer(data, feature_to_repair, repair_level, features_to_ignore=ignored_features)
  repaired_data = repairer.repair(data)

  print "CategoricRepairer no rows lost:", len(repaired_data) == len(data)
  print "CategoricRepairer features repaired for level=1.0:", repaired_data != data


if __name__== "__main__":
  test()
