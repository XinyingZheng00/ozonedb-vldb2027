#include "test_tool.h"
#include <cstring>
#include <functional>
#include <iostream>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

void multipleProcessorTester(int const numProcesses, void (*func)(std::string, int), std::string prefix) {
  int pipe_fd[2];
  if (pipe(pipe_fd) == -1) {
    std::cerr << "Pipe creation failed" << std::endl;
    return;
  }

  for (int i = 0; i < numProcesses; ++i) {
    pid_t pid = fork();
    if (pid < 0) {
      std::cerr << "Fork failed for process " << i << std::endl;
      continue;  // try the next child
    } else if (pid == 0) {
      // Child process
      close(pipe_fd[1]);  // child keeps only the read end

      // Wait for the start signal.
      char buf;
      read(pipe_fd[0], &buf, 1);
      func(prefix, i);
      exit(0);
    } else {
      // Parent: fork the next child.
    }
  }

  // Release all children at once.
  close(pipe_fd[0]);          // parent keeps only the write end
  sleep(1);                   // let every child reach read()
  write(pipe_fd[1], "s", 1);  // start signal
  close(pipe_fd[1]);          // done with the pipe

  // Wait for every child and report failures.

  for (int i = 0; i < numProcesses; ++i) {
    int status;
    pid_t terminated_pid = wait(&status);  // reaps one child
    if (WIFEXITED(status)) {
      if (WEXITSTATUS(status) != 0) {
        std::cerr << "Child process with pid " << terminated_pid << " exited with status " << WEXITSTATUS(status) << std::endl;
      }
    } else {
      int signal_number = WTERMSIG(status);
      std::cerr << "Child process with pid " << terminated_pid << " terminated by signal " << signal_number << std::endl;
      std::cerr << "Signal name: " << strsignal(signal_number) << std::endl;
    }
  }
}
