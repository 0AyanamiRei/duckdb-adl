//===----------------------------------------------------------------------===//
//                         DuckDB
//
// duckdb/optimizer/join_order/adl_opt_join_linearizer.hpp
//
//===----------------------------------------------------------------------===//

#pragma once

#include "duckdb/common/string.hpp"
#include "duckdb/optimizer/join_order/cost_model.hpp"
#include "duckdb/optimizer/join_order/plan_enumerator.hpp"

namespace duckdb {

struct ADLOptJoinLinearizationResult {
	string status;
	idx_t relation_count = 0;
	string summary_json;
	string full_json;
};

class ADLOptJoinLinearizer {
public:
	static ADLOptJoinLinearizationResult Generate(QueryGraphManager &query_graph_manager, CostModel &cost_model,
	                                              idx_t requested_k);
};

} // namespace duckdb
