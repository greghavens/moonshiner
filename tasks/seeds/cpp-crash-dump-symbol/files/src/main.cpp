#include <iostream>

#include "shutdown/runtime.h"

int main() {
  shutdown::AuditLog audit;
  {
    shutdown::Runtime runtime(audit);
    runtime.queue_completion(41);
  }

  for (const auto& entry : audit) {
    std::cout << entry << '\n';
  }
}
