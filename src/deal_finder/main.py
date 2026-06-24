from deal_finder.crew import DealFinderCrew

def run():
    inputs = {
        'theme': 'smart casual, 30 year old male',
        'budget': '400'
    }
    DealFinderCrew().crew().kickoff(inputs=inputs)

if __name__ == "__main__":
    run()