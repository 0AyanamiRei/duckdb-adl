#include "duckdb/optimizer/join_order/neuso_runtime_bridge.hpp"

#include "duckdb/common/enums/expression_type.hpp"
#include "duckdb/common/enums/join_type.hpp"
#include "duckdb/common/enum_util.hpp"
#include "duckdb/common/exception.hpp"
#include "duckdb/common/mutex.hpp"
#include "duckdb/common/string_util.hpp"
#include "duckdb/main/client_context.hpp"
#include "duckdb/main/config.hpp"
#include "duckdb/main/settings.hpp"
#include "duckdb/optimizer/join_order/cost_model.hpp"
#include "duckdb/optimizer/join_order/plan_enumerator.hpp"
#include "duckdb/optimizer/join_order/query_graph_manager.hpp"
#include "duckdb/optimizer/join_order/relation_statistics_helper.hpp"
#include "mbedtls_wrapper.hpp"
#include "yyjson.hpp"

#include "httplib.hpp"

#ifdef DUCKDB_POSIX
#include <fcntl.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

#include <chrono>
#include <thread>

namespace duckdb {

using namespace duckdb_yyjson; // NOLINT

namespace {

static constexpr const char *NEUSO_MODEL_VERSION = "neuso_contract_smoke";

struct NeuSOConfig {
	bool enabled;
	string command;
	string host;
	idx_t port;
	idx_t timeout_ms;
};

struct GraphHashEdge {
	idx_t left;
	idx_t right;
	string join_type;
};

struct NeuSORequest {
	string body;
	string request_id;
	string graph_hash;
};

static string JSONEscape(const string &input) {
	string result;
	result.reserve(input.size() + 8);
	for (auto c : input) {
		switch (c) {
		case '\\':
			result += "\\\\";
			break;
		case '"':
			result += "\\\"";
			break;
		case '\n':
			result += "\\n";
			break;
		case '\r':
			result += "\\r";
			break;
		case '\t':
			result += "\\t";
			break;
		default:
			result += c;
			break;
		}
	}
	return result;
}

static string JSONString(const string &input) {
	return "\"" + JSONEscape(input) + "\"";
}

static string JSONIndexArray(const vector<idx_t> &items) {
	vector<string> rows;
	rows.reserve(items.size());
	for (auto item : items) {
		rows.push_back(to_string(item));
	}
	return "[" + StringUtil::Join(rows, ",") + "]";
}

static string ShortSha256(const string &input) {
	auto hash = duckdb_mbedtls::MbedTlsWrapper::ComputeSha256Hash(input);
	char hash_hex[duckdb_mbedtls::MbedTlsWrapper::SHA256_HASH_LENGTH_TEXT];
	duckdb_mbedtls::MbedTlsWrapper::ToBase16(hash.data(), hash_hex, hash.size());
	return string(hash_hex, 16);
}

static string HTTPBaseURL(const NeuSOConfig &config) {
	return "http://" + config.host + ":" + to_string(config.port);
}

static void ConfigureClient(duckdb_httplib::Client &client, idx_t timeout_ms) {
	auto seconds = static_cast<time_t>(timeout_ms / 1000);
	auto useconds = static_cast<time_t>((timeout_ms % 1000) * 1000);
	if (seconds == 0 && useconds == 0) {
		useconds = 1000;
	}
	client.set_connection_timeout(seconds, useconds);
	client.set_read_timeout(seconds, useconds);
	client.set_write_timeout(seconds, useconds);
	client.set_keep_alive(false);
}

static bool HealthCheck(const NeuSOConfig &config) {
	duckdb_httplib::Client client(HTTPBaseURL(config));
	ConfigureClient(client, MinValue<idx_t>(config.timeout_ms, 250));
	auto result = client.Get("/health");
	return result && result->status == 200;
}

class NeuSOSidecarProcess {
public:
	~NeuSOSidecarProcess() {
#ifdef DUCKDB_POSIX
		if (pid > 0) {
			kill(-pid, SIGTERM);
			kill(pid, SIGTERM);
			int status;
			waitpid(pid, &status, WNOHANG);
		}
#endif
	}

	void EnsureStarted(const NeuSOConfig &config) {
		lock_guard<mutex> guard(lock);
		if (HealthCheck(config)) {
			MarkStarted(config);
			return;
		}
		Start(config);
		auto start = std::chrono::steady_clock::now();
		auto timeout = std::chrono::milliseconds(config.timeout_ms);
		while (std::chrono::steady_clock::now() - start < timeout) {
			if (HealthCheck(config)) {
				MarkStarted(config);
				return;
			}
			std::this_thread::sleep_for(std::chrono::milliseconds(50));
		}
		auto url = HTTPBaseURL(config);
		throw InvalidInputException("NeuSO runtime sidecar did not become healthy at %s", url.c_str());
	}

	bool IsStartedFor(const NeuSOConfig &config) {
		lock_guard<mutex> guard(lock);
		return started && active_command == config.command && active_host == config.host && active_port == config.port;
	}

private:
	void MarkStarted(const NeuSOConfig &config) {
		started = true;
		active_command = config.command;
		active_host = config.host;
		active_port = config.port;
	}

	void Start(const NeuSOConfig &config) {
		if (config.command.empty()) {
			throw InvalidInputException("NeuSO runtime sidecar command is empty");
		}
#ifdef DUCKDB_POSIX
		if (pid > 0) {
			int status;
			auto wait_result = waitpid(pid, &status, WNOHANG);
			if (wait_result == 0) {
				throw InvalidInputException("NeuSO runtime sidecar process is running but health check failed");
			}
			pid = -1;
		}
		auto full_command = config.command + " --host " + config.host + " --port " + to_string(config.port);
		pid = fork();
		if (pid < 0) {
			throw InvalidInputException("Failed to fork NeuSO runtime sidecar");
		}
		if (pid == 0) {
			setsid();
			auto dev_null = open("/dev/null", O_RDWR);
			if (dev_null >= 0) {
				dup2(dev_null, STDIN_FILENO);
				dup2(dev_null, STDOUT_FILENO);
				dup2(dev_null, STDERR_FILENO);
				if (dev_null > STDERR_FILENO) {
					close(dev_null);
				}
			}
			execl("/bin/sh", "sh", "-c", full_command.c_str(), static_cast<char *>(nullptr));
			_exit(127);
		}
#else
		throw NotImplementedException("Automatic NeuSO runtime sidecar startup is only implemented for POSIX builds");
#endif
	}

	mutex lock;
	bool started = false;
	string active_command;
	string active_host;
	idx_t active_port = 0;
#ifdef DUCKDB_POSIX
	pid_t pid = -1;
#endif
};

static NeuSOSidecarProcess &GetSidecarProcess() {
	static NeuSOSidecarProcess process;
	return process;
}

static NeuSOConfig GetConfig(ClientContext &context) {
	NeuSOConfig config;
	config.enabled = Settings::Get<AdlNeusoRuntimeEnabledSetting>(context);
	config.command = Settings::Get<AdlNeusoSidecarCommandSetting>(context);
	config.host = Settings::Get<AdlNeusoSidecarHostSetting>(context);
	config.port = Settings::Get<AdlNeusoSidecarPortSetting>(context);
	config.timeout_ms = Settings::Get<AdlNeusoSidecarTimeoutMsSetting>(context);
	return config;
}

static NeuSOConfig GetConfig(DBConfig &db_config) {
	NeuSOConfig config;
	config.enabled = Settings::Get<AdlNeusoRuntimeEnabledSetting>(db_config);
	config.command = Settings::Get<AdlNeusoSidecarCommandSetting>(db_config);
	config.host = Settings::Get<AdlNeusoSidecarHostSetting>(db_config);
	config.port = Settings::Get<AdlNeusoSidecarPortSetting>(db_config);
	config.timeout_ms = Settings::Get<AdlNeusoSidecarTimeoutMsSetting>(db_config);
	return config;
}

static NeuSOConfig GetStartupConfig(ClientContext &context, bool enabled) {
	auto config = GetConfig(context);
	config.enabled = enabled;
	return config;
}

static NeuSOConfig GetStartupConfig(DBConfig &db_config, bool enabled) {
	auto config = GetConfig(db_config);
	config.enabled = enabled;
	return config;
}

static string ExpressionTypeName(ExpressionType type) {
	switch (type) {
	case ExpressionType::COMPARE_EQUAL:
		return "EQUAL";
	case ExpressionType::COMPARE_LESSTHAN:
		return "LESS_THAN";
	case ExpressionType::COMPARE_GREATERTHAN:
		return "GREATER_THAN";
	case ExpressionType::COMPARE_LESSTHANOREQUALTO:
		return "LESS_THAN_OR_EQUAL";
	case ExpressionType::COMPARE_GREATERTHANOREQUALTO:
		return "GREATER_THAN_OR_EQUAL";
	case ExpressionType::COMPARE_NOTEQUAL:
		return "NOT_EQUAL";
	case ExpressionType::COMPARE_DISTINCT_FROM:
		return "DISTINCT_FROM";
	case ExpressionType::COMPARE_NOT_DISTINCT_FROM:
		return "NOT_DISTINCT_FROM";
	default:
		return ExpressionTypeToString(type);
	}
}

static string RelationDebugLabel(idx_t relation_id, const RelationStats &stats) {
	if (stats.table_name.empty()) {
		return "r" + to_string(relation_id);
	}
	return stats.table_name;
}

static string BuildGraphHashPayload(idx_t relation_count, vector<GraphHashEdge> graph_hash_edges) {
	std::sort(graph_hash_edges.begin(), graph_hash_edges.end(),
	          [](const GraphHashEdge &left, const GraphHashEdge &right) {
		          if (left.left != right.left) {
			          return left.left < right.left;
		          }
		          if (left.right != right.right) {
			          return left.right < right.right;
		          }
		          return left.join_type < right.join_type;
	          });
	string payload = "{\"edges\":[";
	vector<string> edge_rows;
	for (auto &edge : graph_hash_edges) {
		edge_rows.push_back("[" + to_string(edge.left) + "," + to_string(edge.right) + "," +
		                    JSONString(edge.join_type) + "]");
	}
	payload += StringUtil::Join(edge_rows, ",");
	payload += "],\"relations\":[";
	vector<string> relation_ids;
	for (idx_t relation_id = 0; relation_id < relation_count; relation_id++) {
		relation_ids.push_back(to_string(relation_id));
	}
	payload += StringUtil::Join(relation_ids, ",");
	payload += "]}";
	return payload;
}

static NeuSORequest BuildRequestJSON(QueryGraphManager &query_graph_manager, CostModel &cost_model,
                                     const vector<vector<idx_t>> &linear_orders) {
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	auto relation_stats = query_graph_manager.relation_manager.GetRelationStats();
	vector<idx_t> degree(relation_count, 0);
	vector<string> edge_rows;
	vector<GraphHashEdge> graph_hash_edges;
	idx_t edge_id = 0;

	for (auto &filter_info : query_graph_manager.GetFilterBindings()) {
		if (filter_info->join_type != JoinType::INNER) {
			throw InvalidInputException("NeuSO runtime bridge only supports INNER join filters in this phase");
		}
		if (!filter_info->left_set || !filter_info->right_set) {
			continue;
		}
		if (filter_info->left_set->count != 1 || filter_info->right_set->count != 1) {
			throw InvalidInputException("NeuSO runtime bridge only supports pair join graph edges in this phase");
		}
		auto left = filter_info->left_set->relations[0].index;
		auto right = filter_info->right_set->relations[0].index;
		if (left >= relation_count || right >= relation_count) {
			throw InternalException("NeuSO runtime bridge relation id out of range");
		}
		degree[left]++;
		degree[right]++;
		auto &left_set = query_graph_manager.set_manager.GetJoinRelation(RelationIndex(left));
		auto &right_set = query_graph_manager.set_manager.GetJoinRelation(RelationIndex(right));
		auto &pair_set = query_graph_manager.set_manager.Union(left_set, right_set);
		auto pair_cardinality = cost_model.cardinality_estimator.EstimateCardinalityWithSet<idx_t>(pair_set);
		auto left_cardinality = MaxValue<idx_t>(relation_stats[left].cardinality, 1);
		auto right_cardinality = MaxValue<idx_t>(relation_stats[right].cardinality, 1);
		auto denominator = static_cast<double>(left_cardinality) * static_cast<double>(right_cardinality);
		auto selectivity = denominator <= 0 ? 1.0 : static_cast<double>(pair_cardinality) / denominator;

		string edge = "{";
		edge += "\"edge_id\":" + to_string(edge_id++);
		edge += ",\"left_relation_id\":" + to_string(left);
		edge += ",\"right_relation_id\":" + to_string(right);
		edge += ",\"join_type\":\"INNER\"";
		edge += ",\"predicate_type\":" + JSONString(ExpressionTypeName(filter_info->filter->GetExpressionType()));
		edge += ",\"estimated_pair_cardinality\":" + to_string(pair_cardinality);
		edge += ",\"selectivity\":" + to_string(selectivity);
		edge += ",\"estimated_join_cost\":" + to_string(pair_cardinality);
		edge += "}";
		edge_rows.push_back(std::move(edge));
		graph_hash_edges.push_back({left, right, "INNER"});
	}

	if (edge_rows.empty() && relation_count > 1) {
		throw InvalidInputException("NeuSO runtime bridge cannot build a request without join edges");
	}

	vector<string> relation_rows;
	for (idx_t relation_id = 0; relation_id < relation_count; relation_id++) {
		auto label = RelationDebugLabel(relation_id, relation_stats[relation_id]);
		auto cardinality = relation_stats[relation_id].cardinality;
		string relation = "{";
		relation += "\"relation_id\":" + to_string(relation_id);
		relation += ",\"debug_label\":" + JSONString(label);
		relation += ",\"alias\":" + JSONString(label);
		relation += ",\"table\":" + JSONString(label);
		relation += ",\"base_cardinality\":" + to_string(cardinality);
		relation += ",\"estimated_cardinality\":" + to_string(cardinality);
		relation += ",\"degree\":" + to_string(degree[relation_id]);
		relation += "}";
		relation_rows.push_back(std::move(relation));
	}
	vector<string> linear_order_rows;
	linear_order_rows.reserve(linear_orders.size());
	for (idx_t linear_order_id = 0; linear_order_id < linear_orders.size(); linear_order_id++) {
		string linear_order = "{";
		linear_order += "\"linear_order_id\":" + JSONString("ikkbz_root_" + to_string(linear_order_id));
		linear_order += ",\"relation_id_order\":" + JSONIndexArray(linear_orders[linear_order_id]);
		linear_order += "}";
		linear_order_rows.push_back(std::move(linear_order));
	}

	auto request_id = "duckdb_neuso_" + to_string(reinterpret_cast<uintptr_t>(&query_graph_manager));
	auto graph_hash = ShortSha256(BuildGraphHashPayload(relation_count, graph_hash_edges));
	string request = "{";
	request += "\"version\":1";
	request += ",\"request_id\":" + JSONString(request_id);
	request += ",\"graph_hash\":" + JSONString(graph_hash);
	request += ",\"mode\":\"linear_join_order\"";
	request += ",\"scope\":{";
	request += "\"relation_count\":" + to_string(relation_count);
	request += ",\"large_join_threshold\":" + to_string(PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE);
	request += ",\"supported_shape\":\"regular_inner_pair_graph\"";
	request += "}";
	request += ",\"relations\":[" + StringUtil::Join(relation_rows, ",") + "]";
	request += ",\"edges\":[" + StringUtil::Join(edge_rows, ",") + "]";
	if (!linear_orders.empty()) {
		request += ",\"base_linear_order\":" + JSONIndexArray(linear_orders[0]);
		request += ",\"candidate_linear_orders\":[" + StringUtil::Join(linear_order_rows, ",") + "]";
	}
	request += "}";
	return {std::move(request), std::move(request_id), std::move(graph_hash)};
}

static yyjson_val *RequireObjectField(yyjson_val *root, const char *field) {
	auto result = yyjson_obj_get(root, field);
	if (!result) {
		throw InvalidInputException("NeuSO runtime response is missing required field '%s'", field);
	}
	return result;
}

static string RequireStringField(yyjson_val *root, const char *field) {
	auto value = RequireObjectField(root, field);
	if (!yyjson_is_str(value)) {
		throw InvalidInputException("NeuSO runtime response field '%s' must be a string", field);
	}
	return string(yyjson_get_str(value));
}

static idx_t RequireUIntField(yyjson_val *root, const char *field) {
	auto value = RequireObjectField(root, field);
	if (!yyjson_is_uint(value)) {
		throw InvalidInputException("NeuSO runtime response field '%s' must be an unsigned integer", field);
	}
	return yyjson_get_uint(value);
}

static vector<idx_t> ParseJoinOrder(yyjson_val *root) {
	auto value = RequireObjectField(root, "join_order");
	if (!yyjson_is_arr(value)) {
		throw InvalidInputException("NeuSO runtime response field 'join_order' must be an array");
	}
	vector<idx_t> result;
	size_t idx, max;
	yyjson_val *item;
	yyjson_arr_foreach(value, idx, max, item) {
		if (!yyjson_is_uint(item)) {
			throw InvalidInputException("NeuSO runtime response join_order entries must be unsigned integers");
		}
		result.push_back(yyjson_get_uint(item));
	}
	return result;
}

static void ValidateJoinOrder(QueryGraphManager &query_graph_manager, const vector<idx_t> &join_order) {
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	if (join_order.size() != relation_count) {
		throw InvalidInputException("NeuSO runtime join_order length does not match relation count");
	}
	vector<bool> seen(relation_count, false);
	for (auto relation_id : join_order) {
		if (relation_id >= relation_count) {
			throw InvalidInputException("NeuSO runtime join_order contains unknown relation id %llu", relation_id);
		}
		if (seen[relation_id]) {
			throw InvalidInputException("NeuSO runtime join_order contains duplicate relation id %llu", relation_id);
		}
		seen[relation_id] = true;
	}

	for (idx_t pos = 1; pos < join_order.size(); pos++) {
		auto relation_id = join_order[pos];
		bool connected = false;
		auto &relation_set = query_graph_manager.set_manager.GetJoinRelation(RelationIndex(relation_id));
		for (idx_t previous = 0; previous < pos; previous++) {
			auto &joined_set = query_graph_manager.set_manager.GetJoinRelation(RelationIndex(join_order[previous]));
			auto connections = query_graph_manager.GetQueryGraphEdges().GetConnections(relation_set, joined_set);
			if (!connections.empty()) {
				connected = true;
				break;
			}
		}
		if (!connected) {
			throw InvalidInputException("NeuSO runtime join_order append is disconnected at relation %llu",
			                            relation_id);
		}
	}
}

static void ValidateResponse(const string &response_body, QueryGraphManager &query_graph_manager,
                             const NeuSORequest &request) {
	yyjson_read_err error;
	auto parse_buffer = response_body;
	auto doc = yyjson_read_opts(parse_buffer.data(), parse_buffer.size(), YYJSON_READ_NOFLAG, nullptr, &error);
	if (!doc) {
		throw InvalidInputException("NeuSO runtime response is not valid JSON: %s", error.msg);
	}
	unique_ptr<yyjson_doc, void (*)(yyjson_doc *)> doc_guard(doc, yyjson_doc_free);
	auto root = yyjson_doc_get_root(doc);
	if (!yyjson_is_obj(root)) {
		throw InvalidInputException("NeuSO runtime response root must be a JSON object");
	}
	if (RequireUIntField(root, "version") != 1) {
		throw InvalidInputException("NeuSO runtime response version must be 1");
	}
	auto status = RequireStringField(root, "status");
	if (status != "ok") {
		throw InvalidInputException("NeuSO runtime response status is not ok: %s", status.c_str());
	}
	auto model_version = RequireStringField(root, "model_version");
	if (model_version.empty()) {
		throw InvalidInputException("NeuSO runtime response model_version must not be empty");
	}
	auto request_id = RequireStringField(root, "request_id");
	if (request_id != request.request_id) {
		throw InvalidInputException("NeuSO runtime response request_id does not match current request");
	}
	auto graph_hash = RequireStringField(root, "graph_hash");
	if (graph_hash != request.graph_hash) {
		throw InvalidInputException("NeuSO runtime response graph_hash does not match current request");
	}
	auto join_order = ParseJoinOrder(root);
	ValidateJoinOrder(query_graph_manager, join_order);
}

static void InvokeSidecar(QueryGraphManager &query_graph_manager, CostModel &cost_model, const NeuSOConfig &config,
                          const vector<vector<idx_t>> &linear_orders) {
	auto request = BuildRequestJSON(query_graph_manager, cost_model, linear_orders);
	duckdb_httplib::Client client(HTTPBaseURL(config));
	ConfigureClient(client, config.timeout_ms);
	duckdb_httplib::Headers headers = {{"Content-Type", "application/json"}};
	auto response = client.Post("/infer_join_order", headers, request.body, "application/json");
	if (!response) {
		throw InvalidInputException("NeuSO runtime sidecar request failed");
	}
	if (response->status != 200) {
		throw InvalidInputException("NeuSO runtime sidecar returned HTTP status %d: %s", response->status,
		                            response->body.c_str());
	}
	ValidateResponse(response->body, query_graph_manager, request);
}

} // namespace

void NeuSORuntimeBridge::EnsureStarted(ClientContext &context) {
	auto config = GetStartupConfig(context, true);
	GetSidecarProcess().EnsureStarted(config);
}

void NeuSORuntimeBridge::EnsureStarted(DBConfig &db_config) {
	auto config = GetStartupConfig(db_config, true);
	GetSidecarProcess().EnsureStarted(config);
}

void NeuSORuntimeBridge::InvokeIfEnabled(QueryGraphManager &query_graph_manager, CostModel &cost_model,
                                         const vector<vector<idx_t>> &linear_orders) {
	auto config = GetConfig(query_graph_manager.context);
	if (!config.enabled) {
		return;
	}
	auto relation_count = query_graph_manager.relation_manager.NumRelations();
	if (relation_count < PlanEnumerator::THRESHOLD_TO_SWAP_TO_APPROXIMATE) {
		return;
	}
	auto &sidecar_process = GetSidecarProcess();
	if (!sidecar_process.IsStartedFor(config)) {
		sidecar_process.EnsureStarted(config);
	}
	InvokeSidecar(query_graph_manager, cost_model, config, linear_orders);
}

} // namespace duckdb
