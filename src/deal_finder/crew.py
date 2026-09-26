import warnings

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from deal_finder.models import AssessmentList, HuntRequest, LeadList, ShoppingPlan
from deal_finder.progress import Tracker
from deal_finder.tools import ProductPageTool, ShoppingSearchTool, WebSearchTool

# The progress callback is a bound method, which CrewAI cannot checkpoint.
# This project does not use checkpoints, so the warning is noise.
warnings.filterwarnings("ignore", message="method callbacks cannot be serialized")


@CrewBase
class DealFinderCrew:
    """Plans an outfit, hunts for sale prices, checks them and judges value."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, request: HuntRequest, tracker: Tracker | None = None) -> None:
        self.request = request
        self.tracker = tracker or Tracker()

    # ── Agents ────────────────────────────────────────────────

    @agent
    def stylist(self) -> Agent:
        return Agent(config=self.agents_config["stylist"], verbose=True)  # type: ignore[index]

    @agent
    def deal_hunter(self) -> Agent:
        country = self.request.region
        return Agent(
            config=self.agents_config["deal_hunter"],  # type: ignore[index]
            tools=[
                ShoppingSearchTool(country=country, tracker=self.tracker),
                WebSearchTool(country=country, tracker=self.tracker),
            ],
            max_iter=40,
            verbose=True,
        )

    @agent
    def price_checker(self) -> Agent:
        return Agent(
            config=self.agents_config["price_checker"],  # type: ignore[index]
            tools=[ProductPageTool(tracker=self.tracker)],
            max_iter=40,
            verbose=True,
        )

    @agent
    def curator(self) -> Agent:
        return Agent(config=self.agents_config["curator"], verbose=True)  # type: ignore[index]

    # ── Tasks ─────────────────────────────────────────────────

    @task
    def plan_task(self) -> Task:
        return Task(config=self.tasks_config["plan_task"], output_pydantic=ShoppingPlan)  # type: ignore[index]

    @task
    def hunt_task(self) -> Task:
        return Task(config=self.tasks_config["hunt_task"], output_pydantic=LeadList)  # type: ignore[index]

    @task
    def verify_task(self) -> Task:
        return Task(config=self.tasks_config["verify_task"], output_pydantic=LeadList)  # type: ignore[index]

    @task
    def assess_task(self) -> Task:
        return Task(config=self.tasks_config["assess_task"], output_pydantic=AssessmentList)  # type: ignore[index]

    # ── Crew ──────────────────────────────────────────────────

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            task_callback=self.tracker.task_done,
            verbose=True,
        )
