from crewai import Agent, Crew, Task, Process
from crewai.project import CrewBase, agent, task, crew
from crewai_tools import SerperDevTool

search_tool = SerperDevTool()

@CrewBase
class DealFinderCrew():
    """Deal Finder Crew"""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    # ── Agents ────────────────────────────────────────────────
    # Method name must match the YAML key exactly.
    # config=self.agents_config['key'] loads that agent's role/goal/backstory.

    @agent
    def theme_interpreter(self) -> Agent:
        return Agent(
            config=self.agents_config['theme_interpreter'],
            verbose=True
        )

    @agent
    def deal_hunter(self) -> Agent:
        return Agent(
            config=self.agents_config['deal_hunter'],
            tools=[search_tool],   # <-- only this agent gets the search tool
            verbose=True
        )

    @agent
    def curator(self) -> Agent:
        return Agent(
            config=self.agents_config['curator'],
            verbose=True
        )

    @agent
    def reporter(self) -> Agent:
        return Agent(
            config=self.agents_config['reporter'],
            verbose=True
        )

    # ── Tasks ─────────────────────────────────────────────────
    # Method name must match the YAML key exactly.
    # config=self.tasks_config['key'] loads description/expected_output/agent.

    @task
    def interpret_theme_task(self) -> Task:
        return Task(config=self.tasks_config['interpret_theme_task'])

    @task
    def hunt_deals_task(self) -> Task:
        return Task(config=self.tasks_config['hunt_deals_task'])

    @task
    def curate_task(self) -> Task:
        return Task(config=self.tasks_config['curate_task'])

    @task
    def report_task(self) -> Task:
        return Task(config=self.tasks_config['report_task'])

    # ── Crew ──────────────────────────────────────────────────
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,       # auto-collected from @agent methods
            tasks=self.tasks,         # auto-collected from @task methods
            process=Process.sequential,
            verbose=True
        )