// dsent_main: a standalone command-line driver for DSENT (Sun et al., NOCS 2012).
//
// gem5 ships DSENT (ext/dsent) as a Python module. This small main() calls
// the same library entry points so DSENT can run on its own. Build from
// inside gem5/ext/dsent:
//   g++ -O2 -std=c++14 -w -I. $(find . -name '*.cc' ! -name interface.cc) \
//       /path/to/dsent_main.cc -o dsent
// Usage: dsent config.cfg      (prints the config's EvaluateString results)
// Paths inside the config (e.g. ElectricalTechModelFilename) are relative to
// the working directory.
#include <iostream>
#include <map>
#include <string>

#include "DSENT.h"

int main(int argc, char **argv) {
    if (argc != 2) {
        std::cerr << "usage: " << argv[0] << " config.cfg\n";
        return 1;
    }
    std::map<LibUtil::String, LibUtil::String> config;
    DSENT::Model *model = DSENT::initialize(argv[1], config);
    std::map<std::string, double> outputs;
    DSENT::run(config, model, outputs);
    for (const auto &kv : outputs)
        std::cout << kv.first << " = " << kv.second << "\n";
    DSENT::finalize(config, model);
    return 0;
}
