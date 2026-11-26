#include "mujoco_viewer.hpp"
#include "robot_arm_app.hpp"
#include "hakoniwa/robot_runtime/factory/manifest_factory.hpp"

#include <filesystem>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

struct FactoryCall {
    std::string manifest_path;
    std::string service_config;
};

std::optional<FactoryCall> factory_call;

} // namespace

namespace hakoniwa::robot_runtime::factory {

std::unique_ptr<runner::IRunner> ManifestFactory::create(
    const std::string& manifest_path,
    ManifestFactoryConfig config)
{
    factory_call = {manifest_path, config.async_rpc_service_definition_path};
    throw std::runtime_error("Stopped at the manifest factory");
}

} // namespace hakoniwa::robot_runtime::factory

namespace hakoniwa::robot_arm::apps::common {

void run_mujoco_viewer(mjModel*, mjData*, std::atomic_bool&, std::mutex&, ViewerActions)
{
    throw std::logic_error("Unexpected viewer startup");
}

} // namespace hakoniwa::robot_arm::apps::common

int main(int argc, char** argv)
{
    if (argc != 2) {
        return 2;
    }
    const std::string_view test_case(argv[1]);
    const std::string service_config = "./fixtures/service definitions.json";
    std::vector<std::string> arguments{
        "robot-arm-hakoniwa-asset", "--manifest", "arm.json", "--no-viewer"};
    if (test_case != "without-config") {
        arguments.emplace_back("--rpc-service-config");
    }
    if (test_case == "next-option") {
        arguments.emplace_back("--help");
    } else if (test_case == "empty-value") {
        arguments.emplace_back("");
    } else if (test_case == "forward-path") {
        arguments.push_back(service_config);
    } else if (test_case != "missing-value" && test_case != "without-config") {
        return 2;
    }

    std::vector<char*> pointers;
    for (auto& argument : arguments) {
        pointers.push_back(argument.data());
    }
    pointers.push_back(nullptr);

    std::ostringstream errors;
    auto* original = std::cerr.rdbuf(errors.rdbuf());
    const int result = hakoniwa::robot_arm::apps::common::run_robot_arm_app(
        static_cast<int>(arguments.size()), pointers.data());
    std::cerr.rdbuf(original);

    const bool expects_factory = test_case == "forward-path" || test_case == "without-config";
    if (expects_factory) {
        const std::string expected_config = test_case == "forward-path" ? service_config : "";
        if (result == 1 && factory_call &&
            std::filesystem::path(factory_call->manifest_path).filename() == "arm.json" &&
            factory_call->service_config == expected_config) {
            return 0;
        }
    } else if (result == 1 && !factory_call &&
               errors.str().find("--rpc-service-config expects a path") != std::string::npos) {
        return 0;
    }
    std::cerr << "Unexpected result for " << test_case << ": " << result << '\n'
              << errors.str();
    return 1;
}
