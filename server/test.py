from graph.summary_workflow import SummaryNode


def summary_test():
    summary_node = SummaryNode()
    result = summary_node.run("m_1775032564939")
    print(result.subject)
    print(result.paragraphs)
    print(result.next_steps)


if __name__ == "__main__":
    summary_test()