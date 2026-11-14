# Assignment explained simply

## What was the assignment about?

For MAAI904 Assessment 2, we were asked to build a working system in which several AI agents cooperate to solve a problem. We then need to demonstrate the system and explain how it works.

An **AI agent** is a software component with a particular job. For example, one agent could collect information, another could analyse it, and another could check the proposed answer.

The assignment is worth **30% of the course grade** and is due at the **end of Week 11**. Check Moodle for the exact date and any updated instructions. The brief allows groups of two.

## What should the system demonstrate?

- **Coordination:** how the agents decide who should work next.
- **Tools:** how agents obtain or use information, such as searching documents or checking measurements.
- **Memory and context:** how agents remember findings and use information from earlier steps.
- **Communication:** how agents exchange requests and results while working toward a shared goal.

## Which project did we choose?

We chose **a Blackboard system on top of A2A**, one of the suggested topics.

A **Blackboard** is a shared workspace. Imagine several people solving a problem using the same whiteboard: each person can read the existing notes, add findings, and reconsider an explanation when new evidence appears.

**A2A means Agent2Agent.** It is the communication protocol used by the agents to send requests and receive results. The Blackboard holds the shared knowledge, while a controller decides which agent should act next.

## What does our project do?

Our project investigates simulated campus IT problems, such as a slow student portal.

1. The Evidence Agent gathers measurements or logs.
2. The Diagnosis Agent proposes possible explanations.
3. The Review Agent checks whether the evidence supports those explanations.
4. If information is missing, the agents request more evidence and revise their findings.
5. The Response Agent proposes suitable next steps.

Their findings are recorded on the Blackboard so that everyone can use them. These steps can repeat as needed. If the evidence is insufficient, the system should report that the problem remains unresolved.

The campus IT scenario is our chosen example; the assignment does not require this particular use case. Our tools use synthetic data, and the prototype recommends actions without changing real campus systems.

## What do we need to submit?

- A **10–12 minute live presentation and demonstration** explaining the scenario, working system, agent roles, communication flow, and distinctive features.
- A **slide deck with an architecture diagram** showing the main components and connections.
- The **code repository and tool screenshots**.
- A **5–7 page implementation summary** explaining what was built and how it works.

## How is it marked?

| Area | Weight |
| --- | --- |
| Working system and agent coordination | 30% |
| Technical architecture and design | 25% |
| Presentation clarity and delivery | 20% |
| Innovation and realistic use | 15% |
| Teamwork and contributions | 10% |

We need to show a functioning, coordinated system and explain its design clearly. Both members should understand the project and be able to describe their actual contributions.

## Original documents

- [Assessment brief](<MAAI904 Assessment 2 2026.docx>)
- [Blackboard project description](<Project idea 2- Blackboard system on top of A2A.docx>)
